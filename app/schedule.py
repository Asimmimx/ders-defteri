"""Haftalık program: her haftanın programı ayrıdır (week_start = o haftanın pazartesisi)."""
from datetime import date, timedelta

from flask import Blueprint, render_template, request

from .audit import log
from .db import get_db, get_settings
from .holidays import days_info
from .utils import DAYS, MONTHS, minutes_to_hhmm, parse_int, today_iso

bp = Blueprint("schedule", __name__)

BUSY_MSG = "Bu saat dolu olarak işaretli. Önce dolu işaretini kaldırın."


def _grid_settings():
    s = get_settings()
    start = parse_int(s["schedule_start_hour"], 9)
    end = parse_int(s["schedule_end_hour"], 22)
    slot = parse_int(s["slot_minutes"], 60)
    if slot not in (30, 45, 60, 90, 120):
        slot = 60
    start = min(max(start, 0), 23)
    end = min(max(end, start + 1), 24)
    return start, end, slot


def monday_of(value=None):
    """Verilen tarihin (veya bugünün) haftasının pazartesisi."""
    try:
        d = date.fromisoformat(str(value)[:10]) if value else date.today()
    except ValueError:
        d = date.today()
    return d - timedelta(days=d.weekday())


def week_label(monday):
    sunday = monday + timedelta(days=6)
    if monday.month == sunday.month:
        return f"{monday.day}–{sunday.day} {MONTHS[monday.month - 1]} {monday.year}"
    if monday.year == sunday.year:
        return f"{monday.day} {MONTHS[monday.month - 1]} – {sunday.day} {MONTHS[sunday.month - 1]} {monday.year}"
    return f"{monday.day} {MONTHS[monday.month - 1]} {monday.year} – {sunday.day} {MONTHS[sunday.month - 1]} {sunday.year}"


def _week_from(data):
    """İstekteki haftayı doğrula; geçerli bir pazartesi değilse None."""
    raw = str(data.get("week") or data.get("hafta") or "")[:10]
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        return None
    return d.isoformat() if d.weekday() == 0 else None


@bp.route("/program")
def index():
    db = get_db()
    start, end, slot = _grid_settings()
    monday = monday_of(request.args.get("hafta"))
    students = db.execute(
        """SELECT s.id, s.name, s.color, pa.name parent_name FROM students s
           JOIN parents pa ON pa.id = s.parent_id WHERE s.active = 1 AND pa.active = 1
           ORDER BY s.name"""
    ).fetchall()
    this_monday = monday_of()
    return render_template(
        "schedule.html", students=students, start=start, end=end, slot=slot,
        monday=monday.isoformat(), label=week_label(monday),
        prev=(monday - timedelta(days=7)).isoformat(), next=(monday + timedelta(days=7)).isoformat(),
        this_week=this_monday.isoformat(), is_this_week=monday == this_monday,
    )


def _row_json(row):
    return {k: row[k] for k in row.keys()}


@bp.route("/api/program")
def api_list():
    db = get_db()
    week = _week_from(request.args) or monday_of().isoformat()
    monday = date.fromisoformat(week)
    rows = db.execute(
        """SELECT sl.*, s.name student_name, s.color FROM schedule_slots sl
           JOIN students s ON s.id = sl.student_id WHERE sl.week_start = ? ORDER BY sl.day, sl.start_min""",
        (week,),
    ).fetchall()
    busy = db.execute("SELECT * FROM busy_blocks WHERE week_start = ? ORDER BY day, start_min", (week,)).fetchall()
    dates = [(monday + timedelta(days=i)) for i in range(7)]
    prev_week = (monday - timedelta(days=7)).isoformat()
    prev_count = db.execute(
        "SELECT (SELECT COUNT(*) FROM schedule_slots WHERE week_start = ?) + "
        "(SELECT COUNT(*) FROM busy_blocks WHERE week_start = ?)", (prev_week, prev_week)
    ).fetchone()[0]
    return {
        "week": week,
        "label": week_label(monday),
        "today": today_iso(),
        "dates": [{"iso": d.isoformat(), "day": d.day, "month": MONTHS[d.month - 1]} for d in dates],
        "days": days_info(db, dates[0], dates[-1]),
        "slots": [_row_json(r) for r in rows],
        "busy": [_row_json(r) for r in busy],
        "prev_count": prev_count,
    }


def _is_busy(db, week, day, start_min, exclude_id=None):
    return db.execute(
        "SELECT 1 FROM busy_blocks WHERE week_start = ? AND day = ? AND start_min = ? AND id IS NOT ?",
        (week, day, start_min, exclude_id),
    ).fetchone() is not None


def _has_lesson(db, week, day, start_min):
    return db.execute(
        "SELECT 1 FROM schedule_slots WHERE week_start = ? AND day = ? AND start_min = ?", (week, day, start_min)
    ).fetchone() is not None


def _validate(data, partial=False):
    start, end, slot = _grid_settings()
    out = {}
    if "day" in data or not partial:
        day = parse_int(data.get("day"), -1)
        if not 0 <= day <= 6:
            return None, "Geçersiz gün"
        out["day"] = day
    if "start_min" in data or not partial:
        m = parse_int(data.get("start_min"), -1)
        if not (start * 60 <= m < end * 60):
            return None, "Saat program aralığının dışında"
        out["start_min"] = m
    if "lesson_type" in data:
        out["lesson_type"] = "online" if data.get("lesson_type") == "online" else "inperson"
    if "note" in data:
        out["note"] = str(data.get("note") or "")[:200]
    return out, None


def _when(week, day, start_min):
    d = date.fromisoformat(week) + timedelta(days=day)
    return f"{d.day} {MONTHS[d.month - 1]} {DAYS[day]} {minutes_to_hhmm(start_min)}"


def _student_name(db, sid):
    row = db.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()
    return row["name"] if row else f"#{sid}"


# ---------------------------------------------------------- Dersler

@bp.route("/api/program", methods=["POST"])
def api_create():
    data = request.get_json(silent=True) or {}
    db = get_db()
    week = _week_from(data)
    if not week:
        return {"error": "Geçersiz hafta"}, 400
    sid = parse_int(data.get("student_id"))
    if not db.execute("SELECT 1 FROM students WHERE id = ?", (sid,)).fetchone():
        return {"error": "Öğrenci bulunamadı"}, 400
    vals, err = _validate(data)
    if err:
        return {"error": err}, 400
    if _is_busy(db, week, vals["day"], vals["start_min"]):
        return {"error": BUSY_MSG}, 409
    cur = db.execute(
        """INSERT INTO schedule_slots(student_id, week_start, day, start_min, lesson_type, note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (sid, week, vals["day"], vals["start_min"], vals.get("lesson_type", "inperson"), vals.get("note", "")),
    )
    log("Programa ders eklendi", f"{_student_name(db, sid)} · {_when(week, vals['day'], vals['start_min'])}"
        f" · {'online' if vals.get('lesson_type') == 'online' else 'yüz yüze'}", "slot", cur.lastrowid)
    db.commit()
    return {"id": cur.lastrowid}, 201


@bp.route("/api/program/<int:slot_id>", methods=["PATCH"])
def api_update(slot_id):
    data = request.get_json(silent=True) or {}
    vals, err = _validate(data, partial=True)
    if err:
        return {"error": err}, 400
    db = get_db()
    old = db.execute("SELECT * FROM schedule_slots WHERE id = ?", (slot_id,)).fetchone()
    if old is None:
        return {"error": "Bulunamadı"}, 404
    if not vals:
        return {"ok": True}
    day, m = vals.get("day", old["day"]), vals.get("start_min", old["start_min"])
    moved = (day, m) != (old["day"], old["start_min"])
    if moved and _is_busy(db, old["week_start"], day, m):
        return {"error": BUSY_MSG}, 409
    sets = ", ".join(f"{k} = ?" for k in vals)
    db.execute(f"UPDATE schedule_slots SET {sets} WHERE id = ?", (*vals.values(), slot_id))
    name = _student_name(db, old["student_id"])
    if moved:
        log("Programda ders taşındı", f"{name} · {_when(old['week_start'], old['day'], old['start_min'])} → "
            f"{_when(old['week_start'], day, m)}", "slot", slot_id)
    else:
        log("Programda ders düzenlendi", f"{name} · {_when(old['week_start'], old['day'], old['start_min'])}",
            "slot", slot_id)
    db.commit()
    return {"ok": True}


@bp.route("/api/program/<int:slot_id>", methods=["DELETE"])
def api_delete(slot_id):
    db = get_db()
    old = db.execute("SELECT * FROM schedule_slots WHERE id = ?", (slot_id,)).fetchone()
    db.execute("DELETE FROM schedule_slots WHERE id = ?", (slot_id,))
    if old:
        log("Programdan ders silindi", f"{_student_name(db, old['student_id'])} · "
            f"{_when(old['week_start'], old['day'], old['start_min'])}", "slot", slot_id)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------- Haftayı kopyala / temizle

@bp.route("/api/program/kopyala", methods=["POST"])
def api_copy_week():
    """Bir haftanın programını (dersler + dolu saatler) başka bir haftaya kopyalar. Çakışanlar atlanır."""
    data = request.get_json(silent=True) or {}
    target = _week_from(data)
    if not target:
        return {"error": "Geçersiz hafta"}, 400
    try:
        source = date.fromisoformat(str(data.get("from") or "")[:10])
    except ValueError:
        source = date.fromisoformat(target) - timedelta(days=7)
    source = (source - timedelta(days=source.weekday())).isoformat()
    if source == target:
        return {"error": "Aynı hafta"}, 400
    db = get_db()
    added = skipped = 0
    for b in db.execute("SELECT * FROM busy_blocks WHERE week_start = ?", (source,)).fetchall():
        if _is_busy(db, target, b["day"], b["start_min"]) or _has_lesson(db, target, b["day"], b["start_min"]):
            skipped += 1
            continue
        db.execute("INSERT INTO busy_blocks(week_start, day, start_min, label) VALUES (?, ?, ?, ?)",
                   (target, b["day"], b["start_min"], b["label"]))
        added += 1
    for s in db.execute(
        """SELECT sl.* FROM schedule_slots sl JOIN students st ON st.id = sl.student_id
           WHERE sl.week_start = ? AND st.active = 1""", (source,)
    ).fetchall():
        exists = db.execute(
            "SELECT 1 FROM schedule_slots WHERE week_start = ? AND day = ? AND start_min = ? AND student_id = ?",
            (target, s["day"], s["start_min"], s["student_id"]),
        ).fetchone()
        if exists or _is_busy(db, target, s["day"], s["start_min"]):
            skipped += 1
            continue
        db.execute(
            """INSERT INTO schedule_slots(student_id, week_start, day, start_min, lesson_type, note)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (s["student_id"], target, s["day"], s["start_min"], s["lesson_type"], s["note"]),
        )
        added += 1
    if added:
        log("Program kopyalandı", f"{week_label(date.fromisoformat(source))} → {week_label(date.fromisoformat(target))}"
            f" ({added} öğe)", "slot")
    db.commit()
    if not added and not skipped:
        return {"error": "Kopyalanacak hafta boş."}, 409
    return {"added": added, "skipped": skipped}


@bp.route("/api/program/temizle", methods=["POST"])
def api_clear_week():
    data = request.get_json(silent=True) or {}
    week = _week_from(data)
    if not week:
        return {"error": "Geçersiz hafta"}, 400
    db = get_db()
    n1 = db.execute("DELETE FROM schedule_slots WHERE week_start = ?", (week,)).rowcount
    n2 = db.execute("DELETE FROM busy_blocks WHERE week_start = ?", (week,)).rowcount
    if n1 or n2:
        log("Hafta temizlendi", f"{week_label(date.fromisoformat(week))}: {n1} ders, {n2} dolu saat", "slot")
    db.commit()
    return {"lessons": n1, "busy": n2}


# ---------------------------------------------------------- Dolu saatler

@bp.route("/api/dolu", methods=["POST"])
def busy_create():
    """Bir veya art arda birkaç saati dolu işaretler. Ders olan / zaten dolu saatler atlanır."""
    data = request.get_json(silent=True) or {}
    week = _week_from(data)
    if not week:
        return {"error": "Geçersiz hafta"}, 400
    vals, err = _validate(data)
    if err:
        return {"error": err}, 400
    start, end, slot = _grid_settings()
    count = min(max(parse_int(data.get("count"), 1), 1), 48)
    label = str(data.get("label") or "").strip()[:60]
    db = get_db()
    added, skipped = 0, 0
    for i in range(count):
        m = vals["start_min"] + i * slot
        if m >= end * 60:
            break
        if _is_busy(db, week, vals["day"], m) or _has_lesson(db, week, vals["day"], m):
            skipped += 1
            continue
        db.execute("INSERT INTO busy_blocks(week_start, day, start_min, label) VALUES (?, ?, ?, ?)",
                   (week, vals["day"], m, label))
        added += 1
    if added:
        log("Dolu saat eklendi", f"{_when(week, vals['day'], vals['start_min'])}, {added} blok"
            + (f" · {label}" if label else ""), "busy")
    db.commit()
    if not added:
        return {"error": "Bu saatte ders var veya zaten dolu."}, 409
    return {"added": added, "skipped": skipped}, 201


@bp.route("/api/dolu/<int:bid>", methods=["PATCH"])
def busy_update(bid):
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM busy_blocks WHERE id = ?", (bid,)).fetchone()
    if row is None:
        return {"error": "Bulunamadı"}, 404
    vals, err = _validate(data, partial=True)
    if err:
        return {"error": err}, 400
    week = row["week_start"]
    day = vals.get("day", row["day"])
    m = vals.get("start_min", row["start_min"])
    if (day, m) != (row["day"], row["start_min"]):
        if _has_lesson(db, week, day, m):
            return {"error": "Bu saatte ders var."}, 409
        if _is_busy(db, week, day, m, exclude_id=bid):
            return {"error": "Bu saat zaten dolu."}, 409
    label = str(data["label"]).strip()[:60] if "label" in data else row["label"]
    db.execute("UPDATE busy_blocks SET day = ?, start_min = ?, label = ? WHERE id = ?", (day, m, label, bid))
    log("Dolu saat düzenlendi", f"{_when(week, row['day'], row['start_min'])} → {_when(week, day, m)}"
        + (f" · {label}" if label else ""), "busy", bid)
    db.commit()
    return {"ok": True}


@bp.route("/api/dolu/<int:bid>", methods=["DELETE"])
def busy_delete(bid):
    db = get_db()
    row = db.execute("SELECT * FROM busy_blocks WHERE id = ?", (bid,)).fetchone()
    db.execute("DELETE FROM busy_blocks WHERE id = ?", (bid,))
    if row:
        log("Dolu saat silindi", _when(row["week_start"], row["day"], row["start_min"])
            + (f" · {row['label']}" if row["label"] else ""), "busy", bid)
    db.commit()
    return {"ok": True}
