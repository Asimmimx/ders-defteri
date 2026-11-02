import secrets
from datetime import datetime
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, send_from_directory, url_for)

from .db import get_db, get_settings
from .utils import (commission_label, compute_commission, money, fmt_date,
                    normalize_phone, parse_int, parse_money, today_iso)

bp = Blueprint("payments", __name__)

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf", ".gif"}


def effective_commission(student, settings):
    if student["commission_type"] in ("percent", "fixed") and student["commission_value"] is not None:
        return student["commission_type"], student["commission_value"]
    return settings["commission_type"], float(settings["commission_value"] or 0)


def _get_payment(pay_id):
    row = get_db().execute(
        """SELECT p.*, s.name student_name, s.color, s.parent_id, s.commission_type s_ctype,
                  s.commission_value s_cvalue, s.online_price, s.inperson_price, pa.name parent_name, pa.phone parent_phone
           FROM payments p JOIN students s ON s.id = p.student_id JOIN parents pa ON pa.id = s.parent_id
           WHERE p.id = ?""",
        (pay_id,),
    ).fetchone()
    if row is None:
        abort(404)
    return row


def _active_students():
    return get_db().execute(
        """SELECT s.*, pa.name parent_name FROM students s JOIN parents pa ON pa.id = s.parent_id
           WHERE s.active = 1 AND pa.active = 1 ORDER BY pa.name, s.name"""
    ).fetchall()


@bp.route("/odemeler")
def index():
    db = get_db()
    status = request.args.get("durum", "pending")
    q = request.args.get("q", "").strip()
    sql = """SELECT p.*, s.name student_name, s.color, pa.name parent_name, pa.id parent_id
             FROM payments p JOIN students s ON s.id = p.student_id JOIN parents pa ON pa.id = s.parent_id
             WHERE 1 = 1"""
    args = []
    if status in ("pending", "paid", "cancelled"):
        sql += " AND p.status = ?"
        args.append(status)
    elif status == "overdue":
        sql += " AND p.status = 'pending' AND p.due_date < ?"
        args.append(today_iso())
    if q:
        sql += " AND (pa.name LIKE ? OR s.name LIKE ? OR p.description LIKE ?)"
        args += [f"%{q}%"] * 3
    if status == "paid":
        sql += " ORDER BY p.paid_at DESC, p.id DESC"
    else:
        sql += " ORDER BY p.due_date IS NULL, p.due_date, p.id DESC"
    rows = db.execute(sql + " LIMIT 500", args).fetchall()
    total = sum((r["paid_amount"] if r["status"] == "paid" else r["amount"]) or 0 for r in rows)
    return render_template("payments.html", payments=rows, status=status, q=q, total=total, today=today_iso())


def _payment_form(student):
    f = request.form
    online = max(0, parse_int(f.get("online_count"), 0))
    inperson = max(0, parse_int(f.get("inperson_count"), 0))
    auto = online * student["online_price"] + inperson * student["inperson_price"]
    amount = parse_money(f.get("amount"), None)
    if amount is None:
        amount = auto
    if amount <= 0:
        flash("Tutar 0'dan büyük olmalı (ders sayısı girin ya da tutarı elle yazın).", "error")
        return None
    due = f.get("due_date") or None
    if due:
        try:
            datetime.strptime(due, "%Y-%m-%d")
        except ValueError:
            due = None
    return dict(
        online_count=online, inperson_count=inperson, amount=round(amount, 2), due_date=due,
        description=f.get("description", "").strip(), notes=f.get("notes", "").strip(),
    )


@bp.route("/odemeler/yeni", methods=["GET", "POST"])
def new():
    db = get_db()
    students = _active_students()
    if request.method == "POST":
        sid = parse_int(request.form.get("student_id"))
        student = db.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
        if student is None:
            flash("Öğrenci seçin.", "error")
        else:
            data = _payment_form(student)
            if data:
                cur = db.execute(
                    """INSERT INTO payments(student_id, description, online_count, inperson_count, amount, due_date, notes)
                       VALUES (:student_id, :description, :online_count, :inperson_count, :amount, :due_date, :notes)""",
                    dict(data, student_id=sid),
                )
                db.commit()
                flash(f"Ödeme kaydı oluşturuldu: {money(data['amount'])}", "success")
                return redirect(url_for("payments.detail", pay_id=cur.lastrowid))
    return render_template(
        "payment_form.html", payment=None, students=students, form=request.form,
        selected=parse_int(request.args.get("ogrenci") or request.form.get("student_id")),
        today=today_iso(),
    )


@bp.route("/odemeler/<int:pay_id>")
def detail(pay_id):
    payment = _get_payment(pay_id)
    settings = get_settings()
    ctype, cvalue = effective_commission(
        {"commission_type": payment["s_ctype"], "commission_value": payment["s_cvalue"]}, settings
    )
    lessons = payment["online_count"] + payment["inperson_count"]
    preview = compute_commission(payment["amount"], lessons, ctype, cvalue)
    templates = get_db().execute("SELECT * FROM message_templates ORDER BY id").fetchall()
    messages = [
        {"id": t["id"], "name": t["name"], "text": render_message(t["body"], payment)} for t in templates
    ]
    return render_template(
        "payment_detail.html", p=payment, ctype=ctype, cvalue=cvalue, preview=preview,
        clabel=commission_label(ctype, cvalue), messages=messages,
        phone=normalize_phone(payment["parent_phone"]), today=today_iso(),
    )


def render_message(body, p):
    parts = []
    if p["online_count"]:
        parts.append(f"{p['online_count']} online")
    if p["inperson_count"]:
        parts.append(f"{p['inperson_count']} yüz yüze")
    total = p["online_count"] + p["inperson_count"]
    values = {
        "veli": p["parent_name"],
        "ogrenci": p["student_name"],
        "tutar": money(p["amount"]),
        "ders_sayisi": str(total) if total else "",
        "ders_detay": " + ".join(parts),
        "son_odeme": fmt_date(p["due_date"]) if p["due_date"] else "-",
        "aciklama": p["description"],
    }
    text = body
    for k, v in values.items():
        text = text.replace("{" + k + "}", v)
    return text


@bp.route("/odemeler/<int:pay_id>/duzenle", methods=["GET", "POST"])
def edit(pay_id):
    db = get_db()
    payment = _get_payment(pay_id)
    if request.method == "POST":
        student = db.execute("SELECT * FROM students WHERE id = ?", (payment["student_id"],)).fetchone()
        data = _payment_form(student)
        if data:
            db.execute(
                """UPDATE payments SET description = :description, online_count = :online_count,
                   inperson_count = :inperson_count, amount = :amount, due_date = :due_date, notes = :notes
                   WHERE id = :id""",
                dict(data, id=pay_id),
            )
            if payment["status"] == "paid":
                cm = parse_money(request.form.get("commission_amount"), None)
                pa = parse_money(request.form.get("paid_amount"), None)
                if cm is not None:
                    db.execute("UPDATE payments SET commission_amount = ? WHERE id = ?", (cm, pay_id))
                if pa is not None:
                    db.execute("UPDATE payments SET paid_amount = ? WHERE id = ?", (pa, pay_id))
            db.commit()
            flash("Ödeme güncellendi.", "success")
            return redirect(url_for("payments.detail", pay_id=pay_id))
    return render_template("payment_form.html", payment=payment, students=_active_students(),
                           form=request.form or payment, selected=payment["student_id"], today=today_iso())


def _save_receipt(file):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ValueError("Dekont için sadece resim (jpg, png, webp, heic) veya PDF yüklenebilir.")
    name = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(6)}{ext}"
    path = Path(current_app.config["UPLOAD_DIR"]) / name
    file.save(path)
    return name, (file.filename or name)[:200]


@bp.route("/odemeler/<int:pay_id>/tamamla", methods=["POST"])
def complete(pay_id):
    db = get_db()
    payment = _get_payment(pay_id)
    if payment["status"] != "pending":
        flash("Bu ödeme zaten kapatılmış.", "error")
        return redirect(url_for("payments.detail", pay_id=pay_id))
    f = request.form
    settings = get_settings()
    ctype, cvalue = effective_commission(
        {"commission_type": payment["s_ctype"], "commission_value": payment["s_cvalue"]}, settings
    )
    paid_amount = parse_money(f.get("paid_amount"), payment["amount"])
    lessons = payment["online_count"] + payment["inperson_count"]
    commission = parse_money(f.get("commission_amount"), None)
    if commission is None:
        commission = compute_commission(paid_amount, lessons, ctype, cvalue)
    paid_at = f.get("paid_at") or today_iso()
    receipt = (None, None)
    file = request.files.get("receipt")
    if file and file.filename:
        try:
            receipt = _save_receipt(file)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("payments.detail", pay_id=pay_id))
    db.execute(
        """UPDATE payments SET status = 'paid', paid_at = ?, paid_amount = ?, method = ?,
           receipt_path = COALESCE(?, receipt_path), receipt_name = COALESCE(?, receipt_name),
           commission_type = ?, commission_value = ?, commission_amount = ? WHERE id = ?""",
        (paid_at, paid_amount, f.get("method", ""), receipt[0], receipt[1], ctype, cvalue, commission, pay_id),
    )
    db.commit()
    flash(f"Ödeme tamamlandı. Komisyonunuza {money(commission)} eklendi.", "success")
    return redirect(url_for("payments.detail", pay_id=pay_id))


@bp.route("/odemeler/<int:pay_id>/dekont", methods=["POST"])
def upload_receipt(pay_id):
    db = get_db()
    payment = _get_payment(pay_id)
    file = request.files.get("receipt")
    if not file or not file.filename:
        flash("Dosya seçin.", "error")
        return redirect(url_for("payments.detail", pay_id=pay_id))
    try:
        name, original = _save_receipt(file)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("payments.detail", pay_id=pay_id))
    _remove_receipt_file(payment["receipt_path"])
    db.execute("UPDATE payments SET receipt_path = ?, receipt_name = ? WHERE id = ?", (name, original, pay_id))
    db.commit()
    flash("Dekont yüklendi.", "success")
    return redirect(url_for("payments.detail", pay_id=pay_id))


def _remove_receipt_file(name):
    if name:
        path = Path(current_app.config["UPLOAD_DIR"]) / name
        if path.is_file():
            path.unlink()


@bp.route("/odemeler/<int:pay_id>/dekont")
def receipt(pay_id):
    payment = _get_payment(pay_id)
    if not payment["receipt_path"]:
        abort(404)
    return send_from_directory(
        current_app.config["UPLOAD_DIR"], payment["receipt_path"],
        download_name=payment["receipt_name"] or payment["receipt_path"],
        as_attachment=request.args.get("indir") == "1",
    )


@bp.route("/odemeler/<int:pay_id>/geri-al", methods=["POST"])
def reopen(pay_id):
    db = get_db()
    _get_payment(pay_id)
    db.execute(
        """UPDATE payments SET status = 'pending', paid_at = NULL, paid_amount = NULL,
           commission_amount = 0, commission_type = NULL, commission_value = NULL WHERE id = ?""",
        (pay_id,),
    )
    db.commit()
    flash("Ödeme tekrar 'bekliyor' durumuna alındı, komisyon geri düşüldü.", "success")
    return redirect(url_for("payments.detail", pay_id=pay_id))


@bp.route("/odemeler/<int:pay_id>/iptal", methods=["POST"])
def cancel(pay_id):
    db = get_db()
    payment = _get_payment(pay_id)
    if payment["status"] != "pending":
        flash("Sadece bekleyen ödemeler iptal edilebilir.", "error")
    else:
        db.execute("UPDATE payments SET status = 'cancelled' WHERE id = ?", (pay_id,))
        db.commit()
        flash("Ödeme iptal edildi.", "success")
    return redirect(url_for("payments.detail", pay_id=pay_id))


@bp.route("/odemeler/<int:pay_id>/sil", methods=["POST"])
def delete(pay_id):
    db = get_db()
    payment = _get_payment(pay_id)
    _remove_receipt_file(payment["receipt_path"])
    db.execute("DELETE FROM payments WHERE id = ?", (pay_id,))
    db.commit()
    flash("Ödeme kaydı silindi.", "success")
    return redirect(url_for("main.parent_detail", pid=payment["parent_id"]))


@bp.route("/odemeler/<int:pay_id>/hatirlatildi", methods=["POST"])
def mark_reminded(pay_id):
    db = get_db()
    _get_payment(pay_id)
    db.execute("UPDATE payments SET reminder_sent_at = datetime('now', 'localtime') WHERE id = ?", (pay_id,))
    db.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return {"ok": True}
    flash("Hatırlatma gönderildi olarak işaretlendi.", "success")
    return redirect(url_for("payments.detail", pay_id=pay_id))
