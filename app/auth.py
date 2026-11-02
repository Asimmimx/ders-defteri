import time
from collections import defaultdict, deque

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from .audit import log
from .db import get_db

bp = Blueprint("auth", __name__)

PUBLIC_ENDPOINTS = {"auth.login", "static", "public.robots", "public.manifest", "public.service_worker"}

# Basit kaba kuvvet koruması: 15 dakikada 8 hatalı deneme
_failures = defaultdict(deque)
MAX_FAILURES = 8
WINDOW_SECONDS = 15 * 60


def _client_ip():
    return request.headers.get("CF-Connecting-IP") or request.remote_addr or "?"


def _is_locked(ip):
    q = _failures[ip]
    now = time.time()
    while q and now - q[0] > WINDOW_SECONDS:
        q.popleft()
    return len(q) >= MAX_FAILURES


@bp.before_app_request
def load_user():
    g.user = None
    uid = session.get("user_id")
    if uid:
        g.user = get_db().execute(
            "SELECT id, username, full_name FROM users WHERE id = ?", (uid,)
        ).fetchone()
        if g.user is None:
            session.clear()
    # Portal sayfaları kendi girişini yapar (portal.py)
    if g.user is None and request.endpoint not in PUBLIC_ENDPOINTS and not (request.endpoint or "").startswith("portal."):
        if request.path.startswith("/api/"):
            return {"error": "Oturum süresi doldu, tekrar giriş yapın."}, 401
        return redirect(url_for("auth.login", next=(request.full_path.rstrip("?") if request.method == "GET" and request.path != "/" else None)))


def _start_session(key, uid, remember):
    csrf = session.get("_csrf")
    session.clear()
    if csrf:
        session["_csrf"] = csrf
    session[key] = uid
    session.permanent = remember


def _safe_next(default):
    nxt = request.args.get("next") or ""
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else default


@bp.route("/giris", methods=["GET", "POST"])
def login():
    """Tek giriş sayfası: personel (users) yönetim paneline, veli/öğrenci (portal_users) portala gider."""
    if g.user:
        return redirect(url_for("main.dashboard"))
    if session.get("portal_uid"):
        return redirect(url_for("portal.home"))
    if request.method == "POST":
        ip = _client_ip()
        if _is_locked(ip):
            flash("Çok fazla hatalı deneme. Lütfen 15 dakika sonra tekrar deneyin.", "error")
            return render_template("login.html"), 429
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            _failures.pop(ip, None)
            _start_session("user_id", user["id"], remember)
            log("Giriş yapıldı", "", "auth", user=user)
            db.commit()
            nxt = _safe_next(url_for("main.dashboard"))
            return redirect(url_for("main.dashboard") if nxt.startswith("/portal") else nxt)
        puser = db.execute("SELECT * FROM portal_users WHERE username = ?", (username,)).fetchone()
        if puser and puser["active"] and check_password_hash(puser["password_hash"], password):
            _failures.pop(ip, None)
            _start_session("portal_uid", puser["id"], remember)
            db.execute("UPDATE portal_users SET last_login = datetime('now', 'localtime') WHERE id = ?", (puser["id"],))
            log("Portala giriş yapıldı", f"{username} ({'veli' if puser['role'] == 'parent' else 'öğrenci'})",
                "portal", puser["id"], user={"id": None, "username": username, "full_name": f"Portal: {username}"})
            db.commit()
            nxt = _safe_next(url_for("portal.home"))
            return redirect(nxt if nxt.startswith("/portal/") else url_for("portal.home"))
        _failures[ip].append(time.time())
        log("Hatalı giriş denemesi", f"kullanıcı adı: {username[:40]}", "auth", user=None)
        db.commit()
        time.sleep(0.5)
        flash("Kullanıcı adı veya şifre hatalı.", "error")
    return render_template("login.html")


def username_taken(db, username):
    """Personel ve portal hesapları aynı giriş sayfasını kullanır; kullanıcı adı iki tabloda da benzersiz olmalı."""
    return bool(db.execute(
        "SELECT 1 FROM users WHERE username = ? UNION ALL SELECT 1 FROM portal_users WHERE username = ?",
        (username, username)).fetchone())


@bp.route("/cikis", methods=["POST"])
def logout():
    if g.user:
        log("Çıkış yapıldı", "", "auth")
        get_db().commit()
    session.clear()
    return redirect(url_for("auth.login"))
