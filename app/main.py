import re

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .db import get_db, get_settings
from .utils import commission_label, parse_money, today_iso

bp = Blueprint("main", __name__)

PALETTE = ["#4f7cff", "#f2994a", "#27ae60", "#eb5757", "#9b51e0", "#2d9cdb",
           "#f2c94c", "#e056a0", "#16a085", "#8d6e63", "#607d8b", "#ff7043"]


def commission_balance(db):
    earned = db.execute("SELECT COALESCE(SUM(commission_amount), 0) FROM payments WHERE status = 'paid'").fetchone()[0]
    paid_out = db.execute("SELECT COALESCE(SUM(amount), 0) FROM payouts").fetchone()[0]
    return earned, paid_out, round(earned - paid_out, 2)


def summary_stats(db):
    """Özet kartları (ana sayfa ve ödemeler sayfası)."""
    today = today_iso()
    month = today[:7]
    pending = db.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(amount), 0) s FROM payments WHERE status = 'pending'"
    ).fetchone()
    overdue = db.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(amount), 0) s FROM payments "
        "WHERE status = 'pending' AND due_date IS NOT NULL AND due_date < ?", (today,)
    ).fetchone()
    collected = db.execute(
        "SELECT COALESCE(SUM(paid_amount), 0) FROM payments WHERE status = 'paid' AND substr(paid_at, 1, 7) = ?",
        (month,),
    ).fetchone()[0]
    month_commission = db.execute(
        "SELECT COALESCE(SUM(commission_amount), 0) FROM payments WHERE status = 'paid' AND substr(paid_at, 1, 7) = ?",
        (month,),
    ).fetchone()[0]
    _, _, balance = commission_balance(db)
    return dict(pending=pending, overdue=overdue, collected=collected,
                month_commission=month_commission, balance=balance)


@bp.route("/")
def dashboard():
    db = get_db()
    today = today_iso()
    stats = summary_stats(db)
    upcoming = db.execute(
        """SELECT p.*, s.name student_name, s.color, pa.name parent_name, pa.id parent_id
           FROM payments p JOIN students s ON s.id = p.student_id JOIN parents pa ON pa.id = s.parent_id
           WHERE p.status = 'pending'
           ORDER BY p.due_date IS NULL, p.due_date, p.id LIMIT 15"""
    ).fetchall()
    recent = db.execute(
        """SELECT p.*, s.name student_name, s.color, pa.name parent_name
           FROM payments p JOIN students s ON s.id = p.student_id JOIN parents pa ON pa.id = s.parent_id
           WHERE p.status = 'paid' ORDER BY p.paid_at DESC, p.id DESC LIMIT 8"""
    ).fetchall()
    notes = db.execute(
        """SELECT n.*, COALESCE(NULLIF(u.full_name, ''), u.username, '?') author FROM notes n
           LEFT JOIN users u ON u.id = n.user_id WHERE n.done = 0
           ORDER BY n.pinned DESC, n.created_at DESC LIMIT 4"""
    ).fetchall()
    counts = db.execute(
        "SELECT (SELECT COUNT(*) FROM parents WHERE active = 1) parents, "
        "(SELECT COUNT(*) FROM students WHERE active = 1) students"
    ).fetchone()
    return render_template(
        "dashboard.html", stats=stats, notes=notes, upcoming=upcoming, recent=recent, counts=counts, today=today,
    )


# ---------------------------------------------------------------- Veliler

@bp.route("/veliler")
def parents():
    db = get_db()
    q = request.args.get("q", "").strip()
    show = request.args.get("goster", "aktif")
    sql = """SELECT pa.*,
               (SELECT GROUP_CONCAT(name, ', ') FROM students s WHERE s.parent_id = pa.id) children,
               (SELECT COALESCE(SUM(p.amount), 0) FROM payments p JOIN students s ON s.id = p.student_id
                  WHERE s.parent_id = pa.id AND p.status = 'pending') pending_total
             FROM parents pa WHERE 1 = 1"""
    args = []
    if show == "aktif":
        sql += " AND pa.active = 1"
    elif show == "pasif":
        sql += " AND pa.active = 0"
    if q:
        sql += """ AND (pa.name LIKE ? OR pa.phone LIKE ? OR EXISTS
                   (SELECT 1 FROM students s WHERE s.parent_id = pa.id AND s.name LIKE ?))"""
        like = f"%{q}%"
        args += [like, like, like]
    sql += " ORDER BY pa.name COLLATE NOCASE"
    rows = db.execute(sql, args).fetchall()
    return render_template("parents.html", parents=rows, q=q, show=show)


def _parent_form():
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    notes = request.form.get("notes", "").strip()
    active = 1 if request.form.get("active", "1") == "1" else 0
    if not name:
        flash("Veli adı zorunlu.", "error")
        return None
    if phone and not re.fullmatch(r"[\d\s()+\-]{7,20}", phone):
        flash("Telefon numarası geçersiz görünüyor.", "error")
        return None
    return name, phone, notes, active


@bp.route("/veliler/yeni", methods=["GET", "POST"])
def parent_new():
    if request.method == "POST":
        data = _parent_form()
        if data:
            db = get_db()
            cur = db.execute("INSERT INTO parents(name, phone, notes, active) VALUES (?, ?, ?, ?)", data)
            db.commit()
            flash(f"{data[0]} eklendi. Şimdi çocuğunu ekleyebilirsiniz.", "success")
            return redirect(url_for("main.student_new", parent_id=cur.lastrowid))
    return render_template("parent_form.html", parent=None, form=request.form)


def _get_parent(pid):
    row = get_db().execute("SELECT * FROM parents WHERE id = ?", (pid,)).fetchone()
    if row is None:
        abort(404)
    return row


@bp.route("/veliler/<int:pid>")
def parent_detail(pid):
    db = get_db()
    parent = _get_parent(pid)
    settings = get_settings()
    students = db.execute(
        "SELECT * FROM students WHERE parent_id = ? ORDER BY active DESC, name", (pid,)
    ).fetchall()
    payments = db.execute(
        """SELECT p.*, s.name student_name, s.color FROM payments p JOIN students s ON s.id = p.student_id
           WHERE s.parent_id = ? ORDER BY CASE p.status WHEN 'pending' THEN 0 ELSE 1 END,
           COALESCE(p.paid_at, p.due_date, p.created_at) DESC""",
        (pid,),
    ).fetchall()
    pending_total = sum(p["amount"] for p in payments if p["status"] == "pending")
    paid_total = sum(p["paid_amount"] or 0 for p in payments if p["status"] == "paid")
    default_label = commission_label(settings["commission_type"], settings["commission_value"])
    return render_template(
        "parent_detail.html", parent=parent, students=students, payments=payments,
        pending_total=pending_total, paid_total=paid_total, default_label=default_label,
        commission_label=commission_label, today=today_iso(),
    )


@bp.route("/veliler/<int:pid>/duzenle", methods=["GET", "POST"])
def parent_edit(pid):
    parent = _get_parent(pid)
    if request.method == "POST":
        data = _parent_form()
        if data:
            db = get_db()
            db.execute("UPDATE parents SET name = ?, phone = ?, notes = ?, active = ? WHERE id = ?", (*data, pid))
            db.commit()
            flash("Veli bilgileri güncellendi.", "success")
            return redirect(url_for("main.parent_detail", pid=pid))
    return render_template("parent_form.html", parent=parent, form=request.form or parent)


@bp.route("/veliler/<int:pid>/sil", methods=["POST"])
def parent_delete(pid):
    db = get_db()
    parent = _get_parent(pid)
    has_payments = db.execute(
        "SELECT COUNT(*) FROM payments p JOIN students s ON s.id = p.student_id WHERE s.parent_id = ?", (pid,)
    ).fetchone()[0]
    if has_payments:
        flash("Bu velinin ödeme kayıtları var; silmek yerine 'Pasif' yapın (kayıtlar ve komisyon geçmişi korunur).", "error")
        return redirect(url_for("main.parent_detail", pid=pid))
    db.execute("DELETE FROM parents WHERE id = ?", (pid,))
    db.commit()
    flash(f"{parent['name']} silindi.", "success")
    return redirect(url_for("main.parents"))


# ---------------------------------------------------------------- Öğrenciler

def _student_form():
    f = request.form
    name = f.get("name", "").strip()
    if not name:
        flash("Öğrenci adı zorunlu.", "error")
        return None
    color = f.get("color", "#4f7cff").strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = "#4f7cff"
    ctype = f.get("commission_type") or None
    if ctype not in ("percent", "fixed"):
        ctype = None
    cvalue = parse_money(f.get("commission_value"), None) if ctype else None
    if ctype and cvalue is None:
        flash("Özel komisyon değeri girin veya 'Varsayılan' seçin.", "error")
        return None
    return dict(
        name=name, color=color,
        online_price=parse_money(f.get("online_price")),
        inperson_price=parse_money(f.get("inperson_price")),
        commission_type=ctype, commission_value=cvalue,
        notes=f.get("notes", "").strip(),
        active=1 if f.get("active", "1") == "1" else 0,
    )


@bp.route("/veliler/<int:parent_id>/ogrenci/yeni", methods=["GET", "POST"])
def student_new(parent_id):
    parent = _get_parent(parent_id)
    db = get_db()
    if request.method == "POST":
        data = _student_form()
        if data:
            db.execute(
                """INSERT INTO students(parent_id, name, color, online_price, inperson_price,
                   commission_type, commission_value, notes, active)
                   VALUES (:parent_id, :name, :color, :online_price, :inperson_price,
                   :commission_type, :commission_value, :notes, :active)""",
                dict(data, parent_id=parent_id),
            )
            db.commit()
            flash(f"{data['name']} eklendi.", "success")
            return redirect(url_for("main.parent_detail", pid=parent_id))
    used = {r[0] for r in db.execute("SELECT color FROM students WHERE active = 1")}
    suggested = next((c for c in PALETTE if c not in used), PALETTE[0])
    return render_template("student_form.html", parent=parent, student=None,
                           form=request.form, suggested=suggested, palette=PALETTE)


@bp.route("/ogrenciler/<int:sid>/duzenle", methods=["GET", "POST"])
def student_edit(sid):
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
    if student is None:
        abort(404)
    parent = _get_parent(student["parent_id"])
    if request.method == "POST":
        data = _student_form()
        if data:
            db.execute(
                """UPDATE students SET name = :name, color = :color, online_price = :online_price,
                   inperson_price = :inperson_price, commission_type = :commission_type,
                   commission_value = :commission_value, notes = :notes, active = :active WHERE id = :id""",
                dict(data, id=sid),
            )
            db.commit()
            flash("Öğrenci güncellendi.", "success")
            return redirect(url_for("main.parent_detail", pid=parent["id"]))
    return render_template("student_form.html", parent=parent, student=student,
                           form=request.form or student, suggested=student["color"], palette=PALETTE)


@bp.route("/ogrenciler/<int:sid>/sil", methods=["POST"])
def student_delete(sid):
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
    if student is None:
        abort(404)
    if db.execute("SELECT COUNT(*) FROM payments WHERE student_id = ?", (sid,)).fetchone()[0]:
        flash("Bu öğrencinin ödeme kayıtları var; silmek yerine 'Pasif' yapın.", "error")
        return redirect(url_for("main.parent_detail", pid=student["parent_id"]))
    db.execute("DELETE FROM students WHERE id = ?", (sid,))
    db.commit()
    flash(f"{student['name']} silindi.", "success")
    return redirect(url_for("main.parent_detail", pid=student["parent_id"]))
