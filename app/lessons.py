"""Ders kayıtları: derste işlenen konu, ödev ve PDF/görsel dosyaları.

Kayıt ya programdaki bir derse bağlıdır (slot_id) ya da elle girilir (slot_id NULL).
Programdaki ders taşınınca bağlı kaydın tarihi/saati de güncellenir; ders silinince
içi dolu kayıt korunur (bağı kopar), boş kayıt silinir.
"""
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
                   send_from_directory, url_for)

from .audit import log
from .db import get_db
from .utils import MONTHS, DAYS, minutes_to_hhmm, parse_int, today_iso

bp = Blueprint("lessons", __name__)

FILE_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
SUBDIR = "lessons"


# ---------------------------------------------------------------- yardımcılar (schedule.py da kullanır)

def slot_date(week_start, day):
    return (date.fromisoformat(week_start) + timedelta(days=int(day))).isoformat()


def lesson_label(row):
    d = date.fromisoformat(row["lesson_date"])
    txt = f"{d.day} {MONTHS[d.month - 1]} {d.year} {DAYS[d.weekday()]}"
    if row["start_min"] is not None:
        txt += f" {minutes_to_hhmm(row['start_min'])}"
    return txt


def sync_from_slot(db, slot_id):
    """Programdaki ders değişince bağlı ders kaydının tarih/saat/türünü güncelle."""
    s = db.execute("SELECT * FROM schedule_slots WHERE id = ?", (slot_id,)).fetchone()
    if s is None:
        return
    db.execute(
        "UPDATE lessons SET lesson_date = ?, start_min = ?, lesson_type = ?, student_id = ? WHERE slot_id = ?",
        (slot_date(s["week_start"], s["day"]), s["start_min"], s["lesson_type"], s["student_id"], slot_id),
    )


def relink_slot(db, slot_id):
    """Yeni eklenen ders, bağı kopmuş (ör. silinip geri alınmış) bir kayıtla aynı öğrenci/tarih/saatteyse yeniden bağla."""
    s = db.execute("SELECT * FROM schedule_slots WHERE id = ?", (slot_id,)).fetchone()
    if s is None:
        return
    row = db.execute(
        "SELECT id FROM lessons WHERE slot_id IS NULL AND student_id = ? AND lesson_date = ? AND start_min = ?"
        " ORDER BY id DESC LIMIT 1",
        (s["student_id"], slot_date(s["week_start"], s["day"]), s["start_min"]),
    ).fetchone()
    if row:
        db.execute("UPDATE lessons SET slot_id = ? WHERE id = ?", (slot_id, row["id"]))


def _is_empty(db, lesson):
    if (lesson["topic"] or lesson["homework"] or lesson["private_note"]).strip():
        return False
    return not db.execute("SELECT 1 FROM lesson_files WHERE lesson_id = ?", (lesson["id"],)).fetchone()


def before_slot_delete(db, slot_id):
    """Ders programdan silinmeden önce: boş kaydı sil, dolu kaydın bağını kopar (kayıt korunur)."""
    lesson = db.execute("SELECT * FROM lessons WHERE slot_id = ?", (slot_id,)).fetchone()
    if lesson is None:
        return
    if _is_empty(db, lesson):
        db.execute("DELETE FROM lessons WHERE id = ?", (lesson["id"],))
    else:
        db.execute("UPDATE lessons SET slot_id = NULL WHERE id = ?", (lesson["id"],))


def lesson_for_slot(db, slot_id, create=True):
    row = db.execute("SELECT * FROM lessons WHERE slot_id = ?", (slot_id,)).fetchone()
    if row or not create:
        return row
    s = db.execute("SELECT * FROM schedule_slots WHERE id = ?", (slot_id,)).fetchone()
    if s is None:
        return None
    cur = db.execute(
        "INSERT INTO lessons(student_id, slot_id, lesson_date, start_min, lesson_type) VALUES (?, ?, ?, ?, ?)",
        (s["student_id"], slot_id, slot_date(s["week_start"], s["day"]), s["start_min"], s["lesson_type"]),
    )
    return db.execute("SELECT * FROM lessons WHERE id = ?", (cur.lastrowid,)).fetchone()


def set_topic_for_slot(db, slot_id, topic):
    topic = str(topic or "").strip()[:2000]
    lesson = lesson_for_slot(db, slot_id, create=bool(topic))
    if lesson is None or lesson["topic"] == topic:
        return False
    db.execute("UPDATE lessons SET topic = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
               (topic, lesson["id"]))
    return True


def file_dir():
    d = Path(current_app.config["UPLOAD_DIR"]) / SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_files(db, lesson_id, files):
    """Yüklenen dosyaları kaydeder. (eklenen isimler, hata mesajları) döner."""
    added, errors = [], []
    for f in files:
        if not f or not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in FILE_EXT:
            errors.append(f"{f.filename}: sadece PDF veya resim (jpg, png, webp) yüklenebilir.")
            continue
        head = f.stream.read(5)
        f.stream.seek(0)
        if ext == ".pdf" and not head.startswith(b"%PDF"):
            errors.append(f"{f.filename}: geçerli bir PDF değil.")
            continue
        name = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(8)}{ext}"
        path = file_dir() / name
        f.save(path)
        original = Path(f.filename).name[:200]
        db.execute("INSERT INTO lesson_files(lesson_id, path, name, size) VALUES (?, ?, ?, ?)",
                   (lesson_id, f"{SUBDIR}/{name}", original, path.stat().st_size))
        added.append(original)
    return added, errors


def remove_file_from_disk(rel_path):
    if not rel_path:
        return
    base = Path(current_app.config["UPLOAD_DIR"]).resolve()
    p = (base / rel_path).resolve()
    if base in p.parents and p.is_file():
        p.unlink()


def delete_lesson_files(db, lesson_ids):
    if not lesson_ids:
        return
    marks = ",".join("?" * len(lesson_ids))
    for r in db.execute(f"SELECT path FROM lesson_files WHERE lesson_id IN ({marks})", list(lesson_ids)).fetchall():
        remove_file_from_disk(r["path"])


def send_lesson_file(row, download=False):
    return send_from_directory(
        current_app.config["UPLOAD_DIR"], row["path"], download_name=row["name"],
        as_attachment=download, max_age=0,
    )


def files_by_lesson(db, lesson_ids):
    out = {}
    if not lesson_ids:
        return out
    marks = ",".join("?" * len(lesson_ids))
    for f in db.execute(f"SELECT * FROM lesson_files WHERE lesson_id IN ({marks}) ORDER BY id", list(lesson_ids)):
        out.setdefault(f["lesson_id"], []).append(f)
    return out


# ---------------------------------------------------------------- sayfalar

def _students():
    return get_db().execute(
        """SELECT s.id, s.name, s.color, s.active, pa.name parent_name FROM students s
           JOIN parents pa ON pa.id = s.parent_id ORDER BY s.active DESC, s.name"""
    ).fetchall()


def _get(lid):
    row = get_db().execute(
        """SELECT l.*, s.name student_name, s.color, s.parent_id, pa.name parent_name FROM lessons l
           JOIN students s ON s.id = l.student_id JOIN parents pa ON pa.id = s.parent_id WHERE l.id = ?""",
        (lid,),
    ).fetchone()
    if row is None:
        abort(404)
    return row


@bp.route("/dersler")
def index():
    db = get_db()
    sid = parse_int(request.args.get("ogrenci"), 0)
    q = request.args.get("q", "").strip()
    sql = """SELECT l.*, s.name student_name, s.color,
               (SELECT COUNT(*) FROM lesson_files f WHERE f.lesson_id = l.id) file_count
             FROM lessons l JOIN students s ON s.id = l.student_id
             WHERE (l.topic != '' OR l.homework != '' OR l.private_note != ''
                    OR EXISTS (SELECT 1 FROM lesson_files f WHERE f.lesson_id = l.id))"""
    args = []
    if sid:
        sql += " AND l.student_id = ?"
        args.append(sid)
    if q:
        sql += " AND (l.topic LIKE ? OR l.homework LIKE ? OR s.name LIKE ?)"
        args += [f"%{q}%"] * 3
    rows = db.execute(sql + " ORDER BY l.lesson_date DESC, l.start_min DESC LIMIT 300", args).fetchall()

    # Son 3 haftada programda olup konusu girilmemiş geçmiş dersler
    today = date.today()
    start = today - timedelta(days=21)
    monday = start - timedelta(days=start.weekday())
    missing = []
    pend_args = [monday.isoformat()]
    pend_sql = """SELECT sl.*, s.name student_name, s.color FROM schedule_slots sl
                  JOIN students s ON s.id = sl.student_id
                  LEFT JOIN lessons l ON l.slot_id = sl.id
                  WHERE sl.week_start >= ? AND (l.id IS NULL OR (l.topic = '' AND NOT EXISTS
                        (SELECT 1 FROM lesson_files f WHERE f.lesson_id = l.id)))"""
    if sid:
        pend_sql += " AND sl.student_id = ?"
        pend_args.append(sid)
    for s in db.execute(pend_sql, pend_args).fetchall():
        d = slot_date(s["week_start"], s["day"])
        if d < today.isoformat() or (d == today.isoformat() and s["start_min"] <= datetime.now().hour * 60 + datetime.now().minute):
            missing.append(dict(id=s["id"], student_name=s["student_name"], color=s["color"],
                                label=lesson_label({"lesson_date": d, "start_min": s["start_min"]}), sort=(d, s["start_min"])))
    missing.sort(key=lambda x: x["sort"], reverse=True)
    return render_template("lessons.html", rows=rows, students=_students(), sid=sid, q=q,
                           missing=missing[:30], label=lesson_label)


def _form_values(linked=None):
    """Formdaki değerler. linked: programa bağlı ders kaydı; onun tarih/saat/tür alanları
    formda kapalıdır (disabled, tarayıcı göndermez), değerler kayıttan alınır."""
    f = request.form
    texts = dict(
        topic=f.get("topic", "").strip()[:2000],
        homework=f.get("homework", "").strip()[:2000],
        private_note=f.get("private_note", "").strip()[:2000],
    )
    if linked is not None:
        return dict(texts, lesson_date=linked["lesson_date"], start_min=linked["start_min"],
                    lesson_type=linked["lesson_type"]), None
    try:
        d = date.fromisoformat(f.get("lesson_date", ""))
    except ValueError:
        return None, "Geçerli bir tarih seçin."
    t = f.get("start_time", "").strip()
    start_min = None
    if t:
        try:
            hh, mm = t.split(":")[:2]
            start_min = int(hh) * 60 + int(mm)
        except ValueError:
            return None, "Saat geçersiz."
        if not 0 <= start_min < 24 * 60:
            return None, "Saat geçersiz."
    return dict(
        texts, lesson_date=d.isoformat(), start_min=start_min,
        lesson_type="online" if f.get("lesson_type") == "online" else "inperson",
    ), None


@bp.route("/dersler/yeni", methods=["GET", "POST"])
def new():
    db = get_db()
    if request.method == "POST":
        sid = parse_int(request.form.get("student_id"))
        student = db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
        vals, err = _form_values()
        if student is None:
            err = "Öğrenci seçin."
        if err:
            flash(err + (" Seçtiğiniz dosyalar yüklenmedi, yeniden seçin."
                         if any(x.filename for x in request.files.getlist("files")) else ""), "error")
        else:
            cur = db.execute(
                """INSERT INTO lessons(student_id, lesson_date, start_min, lesson_type, topic, homework, private_note)
                   VALUES (:student_id, :lesson_date, :start_min, :lesson_type, :topic, :homework, :private_note)""",
                dict(vals, student_id=sid),
            )
            lid = cur.lastrowid
            added, errors = save_files(db, lid, request.files.getlist("files"))
            for e in errors:
                flash(e, "error")
            row = _get(lid)
            log("Ders kaydı eklendi", f"{student['name']} · {lesson_label(row)}"
                + (f" · {len(added)} dosya" if added else ""), "lesson", lid)
            db.commit()
            flash("Ders kaydı eklendi.", "success")
            return redirect(url_for("lessons.edit", lid=lid))
    return render_template("lesson_form.html", lesson=None, files=[], students=_students(),
                           form=request.form, selected=parse_int(request.args.get("ogrenci"), 0),
                           today=today_iso(), label=lesson_label)


@bp.route("/dersler/program/<int:slot_id>")
def from_slot(slot_id):
    db = get_db()
    lesson = lesson_for_slot(db, slot_id)
    if lesson is None:
        abort(404)
    db.commit()
    return redirect(url_for("lessons.edit", lid=lesson["id"]))


@bp.route("/dersler/<int:lid>", methods=["GET", "POST"])
def edit(lid):
    db = get_db()
    lesson = _get(lid)
    if request.method == "POST":
        # programa bağlı derste tarih/saat/tür programdan gelir
        vals, err = _form_values(lesson if lesson["slot_id"] else None)
        if err:
            flash(err + " Seçtiğiniz dosyalar yüklenmedi, yeniden seçin.", "error")
        else:
            db.execute(
                """UPDATE lessons SET lesson_date = :lesson_date, start_min = :start_min, lesson_type = :lesson_type,
                   topic = :topic, homework = :homework, private_note = :private_note,
                   updated_at = datetime('now', 'localtime') WHERE id = :id""",
                dict(vals, id=lid),
            )
            added, errors = save_files(db, lid, request.files.getlist("files"))
            for e in errors:
                flash(e, "error")
            log("Ders kaydı güncellendi", f"{lesson['student_name']} · {lesson_label(_get(lid))}"
                + (f" · {len(added)} dosya eklendi" if added else ""), "lesson", lid)
            db.commit()
            flash("Ders kaydı kaydedildi.", "success")
            return redirect(url_for("lessons.edit", lid=lid))
    files = db.execute("SELECT * FROM lesson_files WHERE lesson_id = ? ORDER BY id", (lid,)).fetchall()
    return render_template("lesson_form.html", lesson=lesson, files=files, students=_students(),
                           form=request.form or lesson, selected=lesson["student_id"], today=today_iso(),
                           label=lesson_label)


@bp.route("/dersler/<int:lid>/sil", methods=["POST"])
def delete(lid):
    db = get_db()
    lesson = _get(lid)
    delete_lesson_files(db, [lid])
    db.execute("DELETE FROM lessons WHERE id = ?", (lid,))
    log("Ders kaydı silindi", f"{lesson['student_name']} · {lesson_label(lesson)}", "lesson", lid)
    db.commit()
    flash("Ders kaydı silindi" + (" (programdaki ders yerinde duruyor)." if lesson["slot_id"] else "."), "success")
    return redirect(url_for("lessons.index", ogrenci=lesson["student_id"]))


@bp.route("/dersler/dosya/<int:fid>")
def file(fid):
    row = get_db().execute("SELECT * FROM lesson_files WHERE id = ?", (fid,)).fetchone()
    if row is None:
        abort(404)
    return send_lesson_file(row, download=request.args.get("indir") == "1")


@bp.route("/dersler/dosya/<int:fid>/sil", methods=["POST"])
def delete_file(fid):
    db = get_db()
    row = db.execute("SELECT * FROM lesson_files WHERE id = ?", (fid,)).fetchone()
    if row is None:
        abort(404)
    lesson = _get(row["lesson_id"])
    remove_file_from_disk(row["path"])
    db.execute("DELETE FROM lesson_files WHERE id = ?", (fid,))
    log("Ders dosyası silindi", f"{lesson['student_name']} · {lesson_label(lesson)} · {row['name']}", "lesson", lesson["id"])
    db.commit()
    flash(f"{row['name']} silindi.", "success")
    return redirect(url_for("lessons.edit", lid=lesson["id"]))
