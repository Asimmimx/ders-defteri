import csv
import io
import re

from flask import Blueprint, Response, render_template, request

from .audit import log
from .db import get_db
from .main import BALANCE_SQL
from .utils import MONTHS, num, qty, today_iso

bp = Blueprint("reports", __name__)

STATUS_TR = {"pending": "Bekliyor", "paid": "Ödendi", "cancelled": "İptal"}


def _month_arg():
    m = request.args.get("ay", "")
    return m if re.fullmatch(r"\d{4}-\d{2}", m) else today_iso()[:7]


def month_label(m):
    y, mo = m.split("-")
    return f"{MONTHS[int(mo) - 1]} {y}"


def _shift(m, delta):
    y, mo = map(int, m.split("-"))
    mo += delta
    y += (mo - 1) // 12
    mo = (mo - 1) % 12 + 1
    return f"{y:04d}-{mo:02d}"


@bp.route("/raporlar")
def index():
    db = get_db()
    m = _month_arg()
    paid = db.execute(
        """SELECT p.*, s.name student_name, s.color, pa.name parent_name, pa.id parent_id
           FROM payments p JOIN students s ON s.id = p.student_id JOIN parents pa ON pa.id = s.parent_id
           WHERE p.status = 'paid' AND substr(p.paid_at, 1, 7) = ? ORDER BY p.paid_at, p.id""", (m,)
    ).fetchall()
    created = db.execute(
        """SELECT COUNT(*) c, COALESCE(SUM(amount), 0) s, COALESCE(SUM(online_count), 0) on_h,
                  COALESCE(SUM(inperson_count), 0) in_h
           FROM payments WHERE status != 'cancelled' AND substr(created_at, 1, 7) = ?""", (m,)
    ).fetchone()
    by_student = {}
    for p in paid:
        e = by_student.setdefault(p["student_id"], {
            "name": p["student_name"], "parent": p["parent_name"], "color": p["color"],
            "on": 0.0, "inp": 0.0, "paid": 0.0, "commission": 0.0, "count": 0})
        e["on"] += p["online_count"] or 0
        e["inp"] += p["inperson_count"] or 0
        e["paid"] += p["paid_amount"] or 0
        e["commission"] += p["commission_amount"] or 0
        e["count"] += 1
    payouts = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payouts WHERE substr(paid_on, 1, 7) = ?", (m,)
    ).fetchone()[0]
    methods = {}
    for p in paid:
        methods[p["method"] or "—"] = methods.get(p["method"] or "—", 0) + (p["paid_amount"] or 0)
    debts = db.execute(
        f"""SELECT pa.id, pa.name, {BALANCE_SQL.format(parent='pa.id')} balance FROM parents pa
            ORDER BY pa.name"""
    ).fetchall()
    debts = [d for d in debts if abs(d["balance"] or 0) > 0.005]
    return render_template(
        "reports.html", m=m, label=month_label(m), prev=_shift(m, -1), next=_shift(m, 1),
        is_current=m >= today_iso()[:7], paid=paid, created=created,
        by_student=sorted(by_student.values(), key=lambda e: -e["paid"]),
        total_paid=sum(p["paid_amount"] or 0 for p in paid),
        total_commission=sum(p["commission_amount"] or 0 for p in paid),
        payouts=payouts, methods=methods, debts=debts,
    )


def _csv_response(filename, header, rows):
    buf = io.StringIO()
    buf.write("﻿")  # Excel'in Türkçe karakterleri doğru açması için
    w = csv.writer(buf, delimiter=";")
    w.writerow(header)
    w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@bp.route("/raporlar/csv")
def export_csv():
    db = get_db()
    kind = request.args.get("tur", "tahsilat")
    m = request.args.get("ay", "")
    if not re.fullmatch(r"\d{4}-\d{2}", m):
        m = ""
    base = """SELECT p.*, s.name student_name, pa.name parent_name, pa.phone parent_phone
              FROM payments p JOIN students s ON s.id = p.student_id JOIN parents pa ON pa.id = s.parent_id"""
    if kind == "tahsilat":
        sql, args = base + " WHERE p.status = 'paid'", []
        if m:
            sql += " AND substr(p.paid_at, 1, 7) = ?"
            args.append(m)
        rows = db.execute(sql + " ORDER BY p.paid_at, p.id", args).fetchall()
        header = ["Ödeme tarihi", "Veli", "Öğrenci", "Online saat", "Yüz yüze saat", "Tutar", "Ödenen",
                  "Yöntem", "Komisyon", "Açıklama", "Dekont"]
        data = [[r["paid_at"], r["parent_name"], r["student_name"], qty(r["online_count"]),
                 qty(r["inperson_count"]), num(r["amount"]), num(r["paid_amount"]), r["method"],
                 num(r["commission_amount"]), r["description"], "Var" if r["receipt_path"] else ""] for r in rows]
        name = f"tahsilatlar-{m or 'tumu'}.csv"
    else:
        sql, args = base, []
        if m:
            sql += " WHERE substr(p.created_at, 1, 7) = ?"
            args.append(m)
        rows = db.execute(sql + " ORDER BY p.created_at, p.id", args).fetchall()
        header = ["No", "Oluşturulma", "Veli", "Telefon", "Öğrenci", "Online saat", "Yüz yüze saat",
                  "Önceki bakiye", "Tutar", "Son ödeme", "Durum", "Ödeme tarihi", "Ödenen", "Komisyon", "Açıklama"]
        data = [[r["id"], r["created_at"][:10], r["parent_name"], r["parent_phone"], r["student_name"],
                 qty(r["online_count"]), qty(r["inperson_count"]), num(r["carry_in"]), num(r["amount"]),
                 r["due_date"] or "", STATUS_TR.get(r["status"], r["status"]), r["paid_at"] or "",
                 num(r["paid_amount"]), num(r["commission_amount"]), r["description"]] for r in rows]
        name = f"odemeler-{m or 'tumu'}.csv"
    log("Rapor indirildi", name, "report")
    db.commit()
    return _csv_response(name, header, data)


@bp.app_template_filter("ay")
def _ay_filter(m):
    try:
        return month_label(m)
    except (ValueError, IndexError, AttributeError):
        return m

