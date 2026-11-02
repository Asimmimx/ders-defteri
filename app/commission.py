from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .db import get_db
from .main import commission_balance
from .utils import money, parse_money, today_iso

bp = Blueprint("commission", __name__)


@bp.route("/komisyon")
def index():
    db = get_db()
    earned, paid_out, balance = commission_balance(db)
    month = request.args.get("ay", "")
    sql = """SELECT p.id, p.paid_at, p.paid_amount, p.commission_amount, p.commission_type, p.commission_value,
                    s.name student_name, s.color, pa.name parent_name
             FROM payments p JOIN students s ON s.id = p.student_id JOIN parents pa ON pa.id = s.parent_id
             WHERE p.status = 'paid'"""
    args = []
    if month:
        sql += " AND substr(p.paid_at, 1, 7) = ?"
        args.append(month)
    sql += " ORDER BY p.paid_at DESC, p.id DESC LIMIT 300"
    earnings = db.execute(sql, args).fetchall()
    payouts = db.execute("SELECT * FROM payouts ORDER BY paid_on DESC, id DESC LIMIT 300").fetchall()
    monthly = db.execute(
        """SELECT m, SUM(e) earned, SUM(o) paid_out FROM (
             SELECT substr(paid_at, 1, 7) m, commission_amount e, 0 o FROM payments WHERE status = 'paid'
             UNION ALL SELECT substr(paid_on, 1, 7), 0, amount FROM payouts
           ) GROUP BY m ORDER BY m DESC LIMIT 12"""
    ).fetchall()
    return render_template(
        "commission.html", earned=earned, paid_out=paid_out, balance=balance,
        earnings=earnings, payouts=payouts, monthly=monthly, month=month, today=today_iso(),
    )


@bp.route("/komisyon/odeme", methods=["POST"])
def add_payout():
    amount = parse_money(request.form.get("amount"), 0)
    if amount <= 0:
        flash("Geçerli bir tutar girin.", "error")
        return redirect(url_for("commission.index"))
    paid_on = request.form.get("paid_on") or today_iso()
    try:
        datetime.strptime(paid_on, "%Y-%m-%d")
    except ValueError:
        paid_on = today_iso()
    db = get_db()
    db.execute("INSERT INTO payouts(amount, paid_on, note) VALUES (?, ?, ?)",
               (amount, paid_on, request.form.get("note", "").strip()))
    db.commit()
    flash(f"{money(amount)} bakiyeden düşüldü.", "success")
    return redirect(url_for("commission.index"))


@bp.route("/komisyon/odeme/<int:oid>/sil", methods=["POST"])
def delete_payout(oid):
    db = get_db()
    db.execute("DELETE FROM payouts WHERE id = ?", (oid,))
    db.commit()
    flash("Kayıt silindi, bakiye güncellendi.", "success")
    return redirect(url_for("commission.index"))
