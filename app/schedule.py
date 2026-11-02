from flask import Blueprint, render_template, request

from .audit import log
from .db import get_db, get_settings
from .utils import DAYS, minutes_to_hhmm, parse_int

bp = Blueprint("schedule", __name__)


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


@bp.route("/program")
def index():
    db = get_db()
    start, end, slot = _grid_settings()
    students = db.execute(
        """SELECT s.id, s.name, s.color, pa.name parent_name FROM students s
           JOIN parents pa ON pa.id = s.parent_id WHERE s.active = 1 AND pa.active = 1
           ORDER BY s.name"""
    ).fetchall()
    return render_template("schedule.html", students=students, start=start, end=end, slot=slot)


def _slot_json(row):
    return {k: row[k] for k in row.keys()}


@bp.route("/api/program")
def api_list():
    rows = get_db().execute(
        """SELECT sl.*, s.name student_name, s.color FROM schedule_slots sl
           JOIN students s ON s.id = sl.student_id ORDER BY sl.day, sl.start_min"""
    ).fetchall()
    busy = get_db().execute("SELECT * FROM busy_blocks ORDER BY day, start_min").fetchall()
    return {"slots": [_slot_json(r) for r in rows], "busy": [_slot_json(r) for r in busy]}


def _is_busy(db, day, start_min, exclude_id=None):
    row = db.execute("SELECT id FROM busy_blocks WHERE day = ? AND start_min = ? AND id IS NOT ?",
                     (day, start_min, exclude_id)).fetchone()
    return row is not None


def _has_lesson(db, day, start_min):
    return db.execute("SELECT 1 FROM schedule_slots WHERE day = ? AND start_min = ?", (day, start_min)).fetchone() is not None


BUSY_MSG = "Bu saat dolu olarak işaretli. Önce dolu işaretini kaldırın."


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


@bp.route("/api/program", methods=["POST"])
def api_create():
    data = request.get_json(silent=True) or {}
    db = get_db()
    sid = parse_int(data.get("student_id"))
    if not db.execute("SELECT 1 FROM students WHERE id = ?", (sid,)).fetchone():
        return {"error": "Öğrenci bulunamadı"}, 400
    vals, err = _validate(data)
    if err:
        return {"error": err}, 400
    if _is_busy(db, vals["day"], vals["start_min"]):
        return {"error": BUSY_MSG}, 409
    cur = db.execute(
        "INSERT INTO schedule_slots(student_id, day, start_min, lesson_type, note) VALUES (?, ?, ?, ?, ?)",
        (sid, vals["day"], vals["start_min"], vals.get("lesson_type", "inperson"), vals.get("note", "")),
    )
    log("Programa ders eklendi", f"{_student_name(db, sid)} · {_when(vals['day'], vals['start_min'])}"
        f" · {'online' if vals.get('lesson_type') == 'online' else 'yüz yüze'}", "slot", cur.lastrowid)
    db.commit()
    return {"id": cur.lastrowid}, 201


@bp.route("/api/program/<int:slot_id>", methods=["PATCH"])
def api_update(slot_id):
    data = request.get_json(silent=True) or {}
    vals, err = _validate(data, partial=True)
    if err:
        return {"error": err}, 400
    if not vals:
        return {"ok": True}
    db = get_db()
    if "day" in vals and "start_min" in vals and _is_busy(db, vals["day"], vals["start_min"]):
        return {"error": BUSY_MSG}, 409
    old = db.execute("SELECT * FROM schedule_slots WHERE id = ?", (slot_id,)).fetchone()
    sets = ", ".join(f"{k} = ?" for k in vals)
    cur = db.execute(f"UPDATE schedule_slots SET {sets} WHERE id = ?", (*vals.values(), slot_id))
    if old and ("day" in vals or "start_min" in vals):
        log("Programda ders taşındı", f"{_student_name(db, old['student_id'])} · "
            f"{_when(old['day'], old['start_min'])} → {_when(vals.get('day', old['day']), vals.get('start_min', old['start_min']))}",
            "slot", slot_id)
    elif old:
        log("Programda ders düzenlendi", f"{_student_name(db, old['student_id'])} · {_when(old['day'], old['start_min'])}",
            "slot", slot_id)
    db.commit()
    if cur.rowcount == 0:
        return {"error": "Bulunamadı"}, 404
    return {"ok": True}


@bp.route("/api/program/<int:slot_id>", methods=["DELETE"])
def api_delete(slot_id):
    db = get_db()
    old = db.execute("SELECT * FROM schedule_slots WHERE id = ?", (slot_id,)).fetchone()
    db.execute("DELETE FROM schedule_slots WHERE id = ?", (slot_id,))
    if old:
        log("Programdan ders silindi", f"{_student_name(db, old['student_id'])} · {_when(old['day'], old['start_min'])}",
            "slot", slot_id)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------- Dolu saatler

@bp.route("/api/dolu", methods=["POST"])
def busy_create():
    """Bir veya art arda birkaç saati dolu işaretler. Ders olan / zaten dolu saatler atlanır."""
    data = request.get_json(silent=True) or {}
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
        if _is_busy(db, vals["day"], m) or _has_lesson(db, vals["day"], m):
            skipped += 1
            continue
        db.execute("INSERT INTO busy_blocks(day, start_min, label) VALUES (?, ?, ?)", (vals["day"], m, label))
        added += 1
    if added:
        log("Dolu saat eklendi", f"{_when(vals['day'], vals['start_min'])}, {added} blok" + (f" · {label}" if label else ""),
            "busy")
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
    day = vals.get("day", row["day"])
    m = vals.get("start_min", row["start_min"])
    if (day, m) != (row["day"], row["start_min"]):
        if _has_lesson(db, day, m):
            return {"error": "Bu saatte ders var."}, 409
        if _is_busy(db, day, m, exclude_id=bid):
            return {"error": "Bu saat zaten dolu."}, 409
    label = str(data["label"]).strip()[:60] if "label" in data else row["label"]
    db.execute("UPDATE busy_blocks SET day = ?, start_min = ?, label = ? WHERE id = ?", (day, m, label, bid))
    log("Dolu saat düzenlendi", f"{_when(row['day'], row['start_min'])} → {_when(day, m)}" + (f" · {label}" if label else ""),
        "busy", bid)
    db.commit()
    return {"ok": True}


@bp.route("/api/dolu/<int:bid>", methods=["DELETE"])
def busy_delete(bid):
    db = get_db()
    row = db.execute("SELECT * FROM busy_blocks WHERE id = ?", (bid,)).fetchone()
    db.execute("DELETE FROM busy_blocks WHERE id = ?", (bid,))
    if row:
        log("Dolu saat silindi", _when(row["day"], row["start_min"]) + (f" · {row['label']}" if row["label"] else ""),
            "busy", bid)
    db.commit()
    return {"ok": True}



def _when(day, start_min):
    return f"{DAYS[day]} {minutes_to_hhmm(start_min)}"


def _student_name(db, sid):
    row = db.execute("SELECT name FROM students WHERE id = ?", (sid,)).fetchone()
    return row["name"] if row else f"#{sid}"
