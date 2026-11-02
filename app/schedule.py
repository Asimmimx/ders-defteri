from flask import Blueprint, render_template, request

from .db import get_db, get_settings
from .utils import parse_int

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
    return {"slots": [_slot_json(r) for r in rows]}


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
    cur = db.execute(
        "INSERT INTO schedule_slots(student_id, day, start_min, lesson_type, note) VALUES (?, ?, ?, ?, ?)",
        (sid, vals["day"], vals["start_min"], vals.get("lesson_type", "inperson"), vals.get("note", "")),
    )
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
    sets = ", ".join(f"{k} = ?" for k in vals)
    cur = db.execute(f"UPDATE schedule_slots SET {sets} WHERE id = ?", (*vals.values(), slot_id))
    db.commit()
    if cur.rowcount == 0:
        return {"error": "Bulunamadı"}, 404
    return {"ok": True}


@bp.route("/api/program/<int:slot_id>", methods=["DELETE"])
def api_delete(slot_id):
    db = get_db()
    db.execute("DELETE FROM schedule_slots WHERE id = ?", (slot_id,))
    db.commit()
    return {"ok": True}
