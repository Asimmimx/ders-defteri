import secrets
from datetime import datetime
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, send_from_directory, url_for)

from .audit import log
from .db import get_db, get_settings
from .main import BALANCE_SQL, parent_balance, summary_stats
from .utils import (commission_label, compute_commission, money, money_text, fmt_date, normalize_phone,
                    num, parse_hours, parse_int, parse_money, qty, today_iso)

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


def _label(p):
    """İşlem geçmişi için kısa ödeme tanımı."""
    return f"#{p['id']} {p['parent_name']} / {p['student_name']}"


def _active_students():
    return get_db().execute(
        f"""SELECT s.*, pa.name parent_name, {BALANCE_SQL.format(parent='s.parent_id')} parent_balance
            FROM students s JOIN parents pa ON pa.id = s.parent_id
            WHERE s.active = 1 AND pa.active = 1 ORDER BY pa.name, s.name"""
    ).fetchall()


@bp.route("/odemeler")
def index():
    db = get_db()
    status = request.args.get("durum", "all")
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
    elif status == "partial":
        sql += " AND p.status = 'paid' AND p.paid_amount < p.amount - 0.005"
    if q:
        sql += " AND (pa.name LIKE ? OR s.name LIKE ? OR p.description LIKE ?)"
        args += [f"%{q}%"] * 3
    if status in ("paid", "partial"):
        sql += " ORDER BY p.paid_at DESC, p.id DESC"
    elif status == "all":
        sql += (" ORDER BY CASE p.status WHEN 'pending' THEN 0 WHEN 'paid' THEN 1 ELSE 2 END,"
                " CASE WHEN p.status = 'pending' THEN p.due_date END,"
                " COALESCE(p.paid_at, p.due_date, p.created_at) DESC, p.id DESC")
    else:
        sql += " ORDER BY p.due_date IS NULL, p.due_date, p.id DESC"
    rows = db.execute(sql + " LIMIT 500", args).fetchall()
    total = sum((r["paid_amount"] if r["status"] == "paid" else r["amount"]) or 0 for r in rows)
    return render_template("payments.html", payments=rows, status=status, q=q, total=total,
                           today=today_iso(), stats=summary_stats(db))


def _payment_form(units, carry=0.0):
    """units: (online saat ücreti, yüz yüze saat ücreti); carry: önceki bakiyeden aktarılan tutar."""
    f = request.form
    online = parse_hours(f.get("online_count"))
    inperson = parse_hours(f.get("inperson_count"))
    auto = online * units[0] + inperson * units[1] + carry
    amount = parse_money(f.get("amount"), None)
    if amount is None:
        amount = auto
    if amount <= 0:
        flash("Tutar 0'dan büyük olmalı (ders saati girin ya da tutarı elle yazın).", "error")
        return None
    due = f.get("due_date") or None
    if due:
        try:
            datetime.strptime(due, "%Y-%m-%d")
        except ValueError:
            due = None
    return dict(
        online_count=online, inperson_count=inperson, online_unit=units[0], inperson_unit=units[1],
        amount=round(amount, 2), due_date=due, carry_in=round(carry, 2),
        description=f.get("description", "").strip(), notes=f.get("notes", "").strip(),
    )


def _insert_payment(db, student_id, data):
    cur = db.execute(
        """INSERT INTO payments(student_id, description, online_count, inperson_count,
           online_unit, inperson_unit, carry_in, amount, due_date, notes)
           VALUES (:student_id, :description, :online_count, :inperson_count,
           :online_unit, :inperson_unit, :carry_in, :amount, :due_date, :notes)""",
        dict(data, student_id=student_id),
    )
    return cur.lastrowid


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
            carry = parent_balance(db, student["parent_id"]) if request.form.get("include_carry") else 0.0
            data = _payment_form((student["online_price"], student["inperson_price"]), carry)
            if data:
                pay_id = _insert_payment(db, sid, data)
                detail_txt = f"{student['name']}: {money(data['amount'])}"
                if carry:
                    detail_txt += f" (önceki bakiye {money(carry)} dahil)"
                log("Ödeme oluşturuldu", detail_txt, "payment", pay_id)
                db.commit()
                flash(f"Ödeme kaydı oluşturuldu: {money(data['amount'])}", "success")
                return redirect(url_for("payments.detail", pay_id=pay_id))
    return render_template(
        "payment_form.html", payment=None, students=students, form=request.form,
        selected=parse_int(request.args.get("ogrenci") or request.form.get("student_id")),
        today=today_iso(),
    )


@bp.route("/odemeler/<int:pay_id>")
def detail(pay_id):
    db = get_db()
    payment = _get_payment(pay_id)
    settings = get_settings()
    ctype, cvalue = effective_commission(
        {"commission_type": payment["s_ctype"], "commission_value": payment["s_cvalue"]}, settings
    )
    lessons = payment["online_count"] + payment["inperson_count"]
    preview = compute_commission(payment["amount"], lessons, ctype, cvalue)
    templates = db.execute("SELECT * FROM message_templates ORDER BY id").fetchall()
    messages = [
        {"id": t["id"], "name": t["name"], "text": render_message(t["body"], payment)} for t in templates
    ]
    history = db.execute(
        "SELECT * FROM audit_log WHERE entity = 'payment' AND entity_id = ? ORDER BY id DESC LIMIT 20", (pay_id,)
    ).fetchall()
    return render_template(
        "payment_detail.html", p=payment, ctype=ctype, cvalue=cvalue, preview=preview,
        clabel=commission_label(ctype, cvalue), messages=messages, history=history,
        phone=normalize_phone(payment["parent_phone"]), today=today_iso(),
        balance=parent_balance(db, payment["parent_id"]),
    )


def payment_units(p):
    """Ödemedeki saat ücretleri; eski kayıtlarda yoksa öğrencinin güncel tarifesi."""
    on = p["online_unit"] or p["online_price"] or 0
    inp = p["inperson_unit"] or p["inperson_price"] or 0
    return on, inp


def carry_text(carry):
    if carry > 0.005:
        return f"Önceki dönemden kalan: {money_text(carry)}"
    if carry < -0.005:
        return f"Önceki fazla ödemeniz: -{money_text(-carry)}"
    return ""


def render_message(body, p):
    on_unit, in_unit = payment_units(p)
    parts, lines = [], []
    if p["online_count"]:
        parts.append(f"{qty(p['online_count'])} saat online")
        lines.append(f"{qty(p['online_count'])} saat online({qty(p['online_count'])}x{num(on_unit)})")
    if p["inperson_count"]:
        parts.append(f"{qty(p['inperson_count'])} saat yüz yüze")
        lines.append(f"{qty(p['inperson_count'])} saat yüz yüze({qty(p['inperson_count'])}x{num(in_unit)})")
    carry = carry_text(p["carry_in"] or 0)
    if carry:
        lines.append(carry)
    total = p["online_count"] + p["inperson_count"]
    values = {
        "veli": p["parent_name"],
        "ogrenci": p["student_name"],
        "tutar": money_text(p["amount"]),
        "ders_listesi": "\n".join(lines),
        "onceki_bakiye": carry,
        "ders_sayisi": qty(total) if total else "",
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
        data = _payment_form(payment_units(payment), payment["carry_in"] or 0)
        if data:
            db.execute(
                """UPDATE payments SET description = :description, online_count = :online_count,
                   inperson_count = :inperson_count, online_unit = :online_unit, inperson_unit = :inperson_unit,
                   amount = :amount, due_date = :due_date, notes = :notes
                   WHERE id = :id""",
                dict(data, id=pay_id),
            )
            changes = []
            if abs(data["amount"] - payment["amount"]) > 0.005:
                changes.append(f"tutar {money(payment['amount'])} → {money(data['amount'])}")
            if payment["status"] == "paid":
                cm = parse_money(request.form.get("commission_amount"), None)
                pa = parse_money(request.form.get("paid_amount"), None)
                if cm is not None:
                    if abs(cm - payment["commission_amount"]) > 0.005:
                        changes.append(f"komisyon {money(payment['commission_amount'])} → {money(cm)}")
                    db.execute("UPDATE payments SET commission_amount = ? WHERE id = ?", (cm, pay_id))
                if pa is not None:
                    if abs(pa - (payment["paid_amount"] or 0)) > 0.005:
                        changes.append(f"ödenen {money(payment['paid_amount'])} → {money(pa)}")
                    db.execute("UPDATE payments SET paid_amount = ? WHERE id = ?", (pa, pay_id))
            log("Ödeme düzenlendi", _label(payment) + (": " + ", ".join(changes) if changes else ""),
                "payment", pay_id)
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
    if paid_amount <= 0:
        flash("Ödenen tutar 0'dan büyük olmalı.", "error")
        return redirect(url_for("payments.detail", pay_id=pay_id))
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
    diff = round(payment["amount"] - paid_amount, 2)
    msg = f"Ödeme tamamlandı. Komisyonunuza {money(commission)} eklendi."
    detail_txt = f"{_label(payment)}: {money(paid_amount)} ({f.get('method', '') or '—'}), komisyon {money(commission)}"
    new_id = None
    if diff > 0.005:
        detail_txt += f", eksik {money(diff)}"
        if f.get("remainder") == "new":
            # Kalan için hemen yeni bir bekleyen ödeme aç (bakiye oraya aktarılır)
            new_id = _insert_payment(db, payment["student_id"], dict(
                online_count=0, inperson_count=0, online_unit=payment["online_unit"],
                inperson_unit=payment["inperson_unit"], carry_in=diff, amount=diff,
                due_date=payment["due_date"] if (payment["due_date"] or "") >= today_iso() else today_iso(),
                description=f"#{pay_id} kalanı" + (f" ({payment['description']})" if payment["description"] else ""),
                notes="",
            ))
            log("Ödeme oluşturuldu", f"#{pay_id} ödemesinin kalanı: {money(diff)}", "payment", new_id)
            msg += f" Kalan {money(diff)} için yeni ödeme açıldı."
        else:
            msg += f" Eksik kalan {money(diff)} velinin bakiyesine borç olarak yazıldı."
    elif diff < -0.005:
        detail_txt += f", fazla {money(-diff)}"
        msg += f" Fazla ödenen {money(-diff)} velinin bakiyesine alacak olarak yazıldı."
    if receipt[0]:
        detail_txt += ", dekont eklendi"
    log("Ödeme tamamlandı", detail_txt, "payment", pay_id)
    db.commit()
    flash(msg, "success")
    return redirect(url_for("payments.detail", pay_id=new_id or pay_id))


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
    log("Dekont yüklendi", f"{_label(payment)}: {original}", "payment", pay_id)
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
    payment = _get_payment(pay_id)
    db.execute(
        """UPDATE payments SET status = 'pending', paid_at = NULL, paid_amount = NULL,
           commission_amount = 0, commission_type = NULL, commission_value = NULL WHERE id = ?""",
        (pay_id,),
    )
    was = "iptalden geri alındı" if payment["status"] == "cancelled" else \
        f"tahsilat geri alındı ({money(payment['paid_amount'])}, komisyon {money(payment['commission_amount'])})"
    log("Ödeme tekrar bekliyor", f"{_label(payment)}: {was}", "payment", pay_id)
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
        log("Ödeme iptal edildi", f"{_label(payment)}: {money(payment['amount'])}", "payment", pay_id)
        db.commit()
        flash("Ödeme iptal edildi.", "success")
    return redirect(url_for("payments.detail", pay_id=pay_id))


@bp.route("/odemeler/<int:pay_id>/sil", methods=["POST"])
def delete(pay_id):
    db = get_db()
    payment = _get_payment(pay_id)
    _remove_receipt_file(payment["receipt_path"])
    db.execute("DELETE FROM payments WHERE id = ?", (pay_id,))
    log("Ödeme silindi", f"{_label(payment)}: {money(payment['amount'])} ({payment['status']})", "payment", pay_id)
    db.commit()
    flash("Ödeme kaydı silindi.", "success")
    return redirect(url_for("main.parent_detail", pid=payment["parent_id"]))


@bp.route("/odemeler/<int:pay_id>/hatirlatildi", methods=["POST"])
def mark_reminded(pay_id):
    db = get_db()
    payment = _get_payment(pay_id)
    db.execute("UPDATE payments SET reminder_sent_at = datetime('now', 'localtime') WHERE id = ?", (pay_id,))
    log("Hatırlatma gönderildi", _label(payment), "payment", pay_id)
    db.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return {"ok": True}
    flash("Hatırlatma gönderildi olarak işaretlendi.", "success")
    return redirect(url_for("payments.detail", pay_id=pay_id))
