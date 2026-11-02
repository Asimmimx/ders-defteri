"""Veli ve öğrenci portalı (portal.eneskayar.com).

Personel girişinden tamamen ayrıdır: ayrı tablo (portal_users), ayrı oturum anahtarı (portal_uid).
Veli: kendi çocuklarının programı, ders kayıtları/dosyaları, ödemeleri ve bakiyesi.
Öğrenci: sadece kendi programı ve ders kayıtları/dosyaları (ödeme bilgisi yok).
Komisyon, iç notlar ve "sadece bize not" hiçbir zaman gösterilmez.
"""
import time
from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .audit import log
from .auth import _client_ip, _failures, _is_locked
from .db import get_db
from .holidays import days_info
from .lessons import files_by_lesson, lesson_label, send_lesson_file
from .main import parent_balance
from .schedule import monday_of, week_label
from .utils import DAYS, MONTHS, minutes_to_hhmm, parse_int, today_iso

bp = Blueprint("portal", __name__, url_prefix="/portal")

OPEN_ENDPOINTS = {"portal.login"}


def _audit_user():
    """İşlem geçmişinde portal kullanıcısını göstermek için users satırı gibi davranan sözlük."""
    p = g.get("portal")
    if not p:
        return None
    return {"id": None, "username": p["username"], "full_name": f"Portal: {p['display_name']}"}


@bp.before_request
def load_portal_user():
    g.portal = None
    uid = session.get("portal_uid")
    if uid:
        row = get_db().execute(
            """SELECT pu.*, COALESCE(pa.name, sp.name) parent_name, st.name student_name,
                      COALESCE(st.parent_id, pu.parent_id) family_id,
                      CASE WHEN pu.role = 'parent' THEN pa.active ELSE st.active END owner_active
               FROM portal_users pu
               LEFT JOIN parents pa ON pa.id = pu.parent_id
               LEFT JOIN students st ON st.id = pu.student_id
               LEFT JOIN parents sp ON sp.id = st.parent_id
               WHERE pu.id = ?""",
            (uid,),
        ).fetchone()
        if row is None or not row["active"] or not row["owner_active"]:
            session.pop("portal_uid", None)
        else:
            g.portal = dict(row)
            g.portal["display_name"] = row["parent_name"] if row["role"] == "parent" else row["student_name"]
    if g.portal is None and request.endpoint not in OPEN_ENDPOINTS:
        return redirect(url_for("portal.login"))


def _students():
    """Giriş yapan kişinin görebileceği öğrenciler."""
    db = get_db()
    p = g.portal
    if p["role"] == "parent":
        return db.execute("SELECT id, name, color, active FROM students WHERE parent_id = ? ORDER BY active DESC, name",
                          (p["parent_id"],)).fetchall()
    return db.execute("SELECT id, name, color, active FROM students WHERE id = ?", (p["student_id"],)).fetchall()


def _ids(students):
    return [s["id"] for s in students]


def _marks(ids):
    return ",".join("?" * len(ids)) or "NULL"


def _lessons(ids, limit=200, student=None):
    if not ids:
        return [], {}
    sel = [student] if student in ids else ids
    rows = get_db().execute(
        f"""SELECT l.id, l.student_id, l.lesson_date, l.start_min, l.lesson_type, l.topic, l.homework,
                   s.name student_name, s.color
            FROM lessons l JOIN students s ON s.id = l.student_id
            WHERE l.student_id IN ({_marks(sel)}) AND l.lesson_date <= ?
              AND (l.topic != '' OR l.homework != '' OR EXISTS (SELECT 1 FROM lesson_files f WHERE f.lesson_id = l.id))
            ORDER BY l.lesson_date DESC, l.start_min DESC LIMIT ?""",
        (*sel, today_iso(), limit),
    ).fetchall()
    return rows, files_by_lesson(get_db(), [r["id"] for r in rows])


def _week_slots(ids, monday):
    if not ids:
        return []
    return get_db().execute(
        f"""SELECT sl.day, sl.start_min, sl.lesson_type, sl.student_id, s.name student_name, s.color
            FROM schedule_slots sl JOIN students s ON s.id = sl.student_id
            WHERE sl.week_start = ? AND sl.student_id IN ({_marks(ids)}) ORDER BY sl.day, sl.start_min""",
        (monday.isoformat(), *ids),
    ).fetchall()


def _week_days(ids, monday):
    """Haftanın günleri: [{date, name, label, slots, info}]"""
    slots = _week_slots(ids, monday)
    dates = [monday + timedelta(days=i) for i in range(7)]
    info = days_info(get_db(), dates[0], dates[-1])
    out = []
    for i, d in enumerate(dates):
        out.append(dict(
            iso=d.isoformat(), name=DAYS[i], label=f"{d.day} {MONTHS[d.month - 1]}",
            slots=[s for s in slots if s["day"] == i], info=info.get(d.isoformat(), []),
        ))
    return out


@bp.app_template_global()
def portal_hhmm(m):
    return minutes_to_hhmm(m) if m is not None else ""


# ---------------------------------------------------------------- giriş / çıkış

@bp.route("/giris", methods=["GET", "POST"])
def login():
    if g.portal:
        return redirect(url_for("portal.home"))
    if request.method == "POST":
        ip = _client_ip()
        if _is_locked(ip):
            flash("Çok fazla hatalı deneme. Lütfen 15 dakika sonra tekrar deneyin.", "error")
            return render_template("portal/login.html"), 429
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM portal_users WHERE username = ?", (username,)).fetchone()
        if user and user["active"] and check_password_hash(user["password_hash"], password):
            _failures.pop(ip, None)
            csrf = session.get("_csrf")
            session.clear()
            if csrf:
                session["_csrf"] = csrf
            session["portal_uid"] = user["id"]
            session.permanent = bool(request.form.get("remember"))
            db.execute("UPDATE portal_users SET last_login = datetime('now', 'localtime') WHERE id = ?", (user["id"],))
            log("Portala giriş yapıldı", f"{username} ({'veli' if user['role'] == 'parent' else 'öğrenci'})",
                "portal", user["id"], user={"id": None, "username": username, "full_name": f"Portal: {username}"})
            db.commit()
            return redirect(url_for("portal.home"))
        _failures[ip].append(time.time())
        log("Portal: hatalı giriş denemesi", f"kullanıcı adı: {username[:40]}", "portal",
            user={"id": None, "username": "", "full_name": ""})
        db.commit()
        time.sleep(0.5)
        flash("Kullanıcı adı veya şifre hatalı.", "error")
    return render_template("portal/login.html")


@bp.route("/cikis", methods=["POST"])
def logout():
    session.pop("portal_uid", None)
    return redirect(url_for("portal.login"))


@bp.route("/sifre", methods=["GET", "POST"])
def password():
    if request.method == "POST":
        db = get_db()
        user = db.execute("SELECT * FROM portal_users WHERE id = ?", (g.portal["id"],)).fetchone()
        new = request.form.get("new", "")
        if not check_password_hash(user["password_hash"], request.form.get("current", "")):
            flash("Mevcut şifre hatalı.", "error")
        elif len(new) < 8:
            flash("Yeni şifre en az 8 karakter olmalı.", "error")
        elif new != request.form.get("new2"):
            flash("Yeni şifreler eşleşmiyor.", "error")
        else:
            db.execute("UPDATE portal_users SET password_hash = ? WHERE id = ?", (generate_password_hash(new), user["id"]))
            log("Portal şifresi değiştirildi", user["username"], "portal", user["id"], user=_audit_user())
            db.commit()
            flash("Şifreniz değiştirildi.", "success")
            return redirect(url_for("portal.home"))
    return render_template("portal/password.html")


# ---------------------------------------------------------------- sayfalar

@bp.route("/")
def home():
    students = _students()
    ids = _ids(students)
    monday = monday_of()
    today = date.today()
    days = [d for d in _week_days(ids, monday) if d["iso"] >= today.isoformat()]
    next_days = _week_days(ids, monday + timedelta(days=7))
    lessons, files = _lessons(ids, limit=5)
    pay = None
    if g.portal["role"] == "parent":
        db = get_db()
        pending = db.execute(
            f"""SELECT COUNT(*) c, COALESCE(SUM(amount), 0) s FROM payments
                WHERE status = 'pending' AND student_id IN ({_marks(ids)})""", ids).fetchone()
        pay = dict(pending=pending, balance=parent_balance(db, g.portal["parent_id"]))
    return render_template("portal/home.html", students=students, days=days, next_days=next_days,
                           rows=lessons, files=files, pay=pay, label=lesson_label)


@bp.route("/program")
def program():
    students = _students()
    monday = monday_of(request.args.get("hafta"))
    this_monday = monday_of()
    return render_template(
        "portal/program.html", days=_week_days(_ids(students), monday), label=week_label(monday),
        prev=(monday - timedelta(days=7)).isoformat(), next=(monday + timedelta(days=7)).isoformat(),
        this_week=this_monday.isoformat(), is_this_week=monday == this_monday, today=today_iso(),
        multi=len(students) > 1,
    )


@bp.route("/dersler")
def lessons():
    students = _students()
    sid = parse_int(request.args.get("ogrenci"), 0)
    rows, files = _lessons(_ids(students), limit=300, student=sid or None)
    return render_template("portal/lessons.html", rows=rows, files=files, students=students, sid=sid,
                           label=lesson_label)


@bp.route("/dosya/<int:fid>")
def file(fid):
    ids = _ids(_students())
    row = get_db().execute(
        "SELECT f.*, l.student_id FROM lesson_files f JOIN lessons l ON l.id = f.lesson_id WHERE f.id = ?", (fid,)
    ).fetchone()
    if row is None or row["student_id"] not in ids:
        abort(404)
    return send_lesson_file(row, download=request.args.get("indir") == "1")


@bp.route("/odemeler")
def payments():
    if g.portal["role"] != "parent":
        abort(404)
    students = _students()
    ids = _ids(students)
    db = get_db()
    rows = db.execute(
        f"""SELECT p.id, p.description, p.online_count, p.inperson_count, p.amount, p.due_date, p.status,
                   p.paid_at, p.paid_amount, p.method, s.name student_name, s.color
            FROM payments p JOIN students s ON s.id = p.student_id
            WHERE p.student_id IN ({_marks(ids)}) AND p.status != 'cancelled'
            ORDER BY CASE p.status WHEN 'pending' THEN 0 ELSE 1 END,
                     COALESCE(p.paid_at, p.due_date, p.created_at) DESC LIMIT 300""",
        ids,
    ).fetchall()
    pending = sum(r["amount"] for r in rows if r["status"] == "pending")
    paid = sum(r["paid_amount"] or 0 for r in rows if r["status"] == "paid")
    return render_template("portal/payments.html", rows=rows, pending=pending, paid=paid,
                           balance=parent_balance(db, g.portal["parent_id"]), today=today_iso())
