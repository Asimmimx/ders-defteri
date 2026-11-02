import time
from collections import defaultdict, deque

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from .db import get_db

bp = Blueprint("auth", __name__)

PUBLIC_ENDPOINTS = {"auth.login", "static"}

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
    if g.user is None and request.endpoint not in PUBLIC_ENDPOINTS:
        if request.path.startswith("/api/"):
            return {"error": "Oturum süresi doldu, tekrar giriş yapın."}, 401
        return redirect(url_for("auth.login", next=(request.full_path.rstrip("?") if request.method == "GET" and request.path != "/" else None)))


@bp.route("/giris", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        ip = _client_ip()
        if _is_locked(ip):
            flash("Çok fazla hatalı deneme. Lütfen 15 dakika sonra tekrar deneyin.", "error")
            return render_template("login.html"), 429
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            _failures.pop(ip, None)
            csrf = session.get("_csrf")
            session.clear()
            if csrf:
                session["_csrf"] = csrf
            session["user_id"] = user["id"]
            session.permanent = bool(request.form.get("remember"))
            nxt = request.args.get("next") or ""
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = url_for("main.dashboard")
            return redirect(nxt)
        _failures[ip].append(time.time())
        time.sleep(0.5)
        flash("Kullanıcı adı veya şifre hatalı.", "error")
    return render_template("login.html")


@bp.route("/cikis", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
