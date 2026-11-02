import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, abort, g, request, session
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db, utils

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file():
    """Basit .env okuyucu (ek bağımlılık olmadan)."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def create_app():
    _load_env_file()
    data_dir = Path(os.environ.get("DATA_DIR", BASE_DIR / "data")).resolve()

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or "dev-insecure-key-change-me",
        DATA_DIR=str(data_dir),
        DATABASE=str(data_dir / "odeme.db"),
        UPLOAD_DIR=str(data_dir / "uploads"),
        BACKUP_DIR=str(data_dir / "backups"),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )
    if app.config["SECRET_KEY"].startswith("dev-") and os.environ.get("COOKIE_SECURE") == "1":
        raise RuntimeError("Canlı ortamda SECRET_KEY ayarlanmalı (.env).")

    # Cloudflare Tunnel arkasında doğru şema/IP için
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    for d in (data_dir, app.config["UPLOAD_DIR"], app.config["BACKUP_DIR"]):
        Path(d).mkdir(parents=True, exist_ok=True)
    db.init_db(app.config["DATABASE"])
    app.teardown_appcontext(db.close_db)

    # CSRF koruması
    def csrf_token():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_urlsafe(32)
        return session["_csrf"]

    @app.before_request
    def check_csrf():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            token = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
            if not token or token != session.get("_csrf"):
                abort(400, "Güvenlik doğrulaması başarısız. Sayfayı yenileyip tekrar deneyin.")

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        # Arama motorları ve yapay zekâ botları indekslemesin
        resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet, noimageindex, noai, noimageai"
        return resp

    app.jinja_env.globals.update(
        csrf_token=csrf_token,
        DAYS=utils.DAYS,
        METHODS=utils.METHODS,
        hhmm=utils.minutes_to_hhmm,
    )
    app.jinja_env.filters.update(
        money=utils.money,
        num=utils.num,
        qty=utils.qty,
        tarih=utils.fmt_date,
        tarihsaat=utils.fmt_datetime,
        wa_phone=lambda p: utils.normalize_phone(p).lstrip("+"),
    )

    from .auth import bp as auth_bp
    from .main import bp as main_bp
    from .payments import bp as payments_bp
    from .commission import bp as commission_bp
    from .schedule import bp as schedule_bp
    from .settings import bp as settings_bp
    from .notes import bp as notes_bp
    from .reports import bp as reports_bp
    from .public import bp as public_bp
    from .lessons import bp as lessons_bp
    from .portal import bp as portal_bp
    from .access import bp as access_bp
    from .gcal import bp as gcal_bp

    for bp in (auth_bp, main_bp, payments_bp, commission_bp, schedule_bp, notes_bp, reports_bp, settings_bp,
               public_bp, lessons_bp, portal_bp, access_bp, gcal_bp):
        app.register_blueprint(bp)

    from flask import render_template
    from werkzeug.exceptions import HTTPException

    messages = {404: "Sayfa bulunamadı.", 413: "Dosya çok büyük (en fazla 25 MB).", 500: "Beklenmeyen bir hata oluştu."}

    @app.errorhandler(HTTPException)
    def http_error(e):
        if request.path.startswith("/api/"):
            return {"error": e.description}, e.code
        msg = messages.get(e.code) if e.code in (404, 413) else e.description
        if g.get("user") is None:
            return f"<h1>{e.code}</h1><p>{msg}</p><a href='/'>Ana sayfa</a>", e.code
        return render_template("error.html", code=e.code, message=msg), e.code

    return app
