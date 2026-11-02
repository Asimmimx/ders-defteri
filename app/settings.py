import re
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, send_from_directory, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import backup
from .db import get_db, get_settings, set_setting
from .utils import commission_label, parse_int, parse_money

bp = Blueprint("settings", __name__)


@bp.route("/ayarlar")
def index():
    db = get_db()
    s = get_settings()
    return render_template(
        "settings.html", s=s,
        clabel=commission_label(s["commission_type"], s["commission_value"]),
        templates=db.execute("SELECT * FROM message_templates ORDER BY id").fetchall(),
        users=db.execute("SELECT id, username, full_name, created_at FROM users ORDER BY id").fetchall(),
        backups=backup.list_backups(current_app.config["BACKUP_DIR"]),
        backup_total=sum(b["size"] for b in backup.list_backups(current_app.config["BACKUP_DIR"])),
    )


@bp.route("/ayarlar/komisyon", methods=["POST"])
def save_commission():
    ctype = request.form.get("commission_type")
    if ctype not in ("percent", "fixed"):
        flash("Komisyon türü seçin.", "error")
        return redirect(url_for("settings.index"))
    value = parse_money(request.form.get("commission_value"), -1)
    if value < 0 or (ctype == "percent" and value > 100):
        flash("Geçerli bir komisyon değeri girin.", "error")
        return redirect(url_for("settings.index"))
    set_setting("commission_type", ctype)
    set_setting("commission_value", value)
    get_db().commit()
    flash(f"Varsayılan komisyon: {commission_label(ctype, value)}. "
          "(Sadece bundan sonra tamamlanan ödemelere uygulanır.)", "success")
    return redirect(url_for("settings.index"))


@bp.route("/ayarlar/program", methods=["POST"])
def save_schedule():
    start = parse_int(request.form.get("schedule_start_hour"), 9)
    end = parse_int(request.form.get("schedule_end_hour"), 22)
    slot = parse_int(request.form.get("slot_minutes"), 60)
    if not (0 <= start < end <= 24) or slot not in (30, 45, 60, 90, 120):
        flash("Geçersiz program ayarı.", "error")
        return redirect(url_for("settings.index"))
    set_setting("schedule_start_hour", start)
    set_setting("schedule_end_hour", end)
    set_setting("slot_minutes", slot)
    get_db().commit()
    flash("Program ayarları kaydedildi.", "success")
    return redirect(url_for("settings.index"))


# ------------------------------------------------------------ SMS taslakları

@bp.route("/ayarlar/taslak", methods=["POST"])
@bp.route("/ayarlar/taslak/<int:tid>", methods=["POST"])
def save_template(tid=None):
    name = request.form.get("name", "").strip()
    body = request.form.get("body", "").strip()
    if not name or not body:
        flash("Taslak adı ve metni zorunlu.", "error")
        return redirect(url_for("settings.index") + "#taslaklar")
    db = get_db()
    if tid:
        db.execute("UPDATE message_templates SET name = ?, body = ? WHERE id = ?", (name, body, tid))
    else:
        db.execute("INSERT INTO message_templates(name, body) VALUES (?, ?)", (name, body))
    db.commit()
    flash("Taslak kaydedildi.", "success")
    return redirect(url_for("settings.index") + "#taslaklar")


@bp.route("/ayarlar/taslak/<int:tid>/sil", methods=["POST"])
def delete_template(tid):
    db = get_db()
    db.execute("DELETE FROM message_templates WHERE id = ?", (tid,))
    db.commit()
    flash("Taslak silindi.", "success")
    return redirect(url_for("settings.index") + "#taslaklar")


# ------------------------------------------------------------ Kullanıcılar

@bp.route("/ayarlar/sifre", methods=["POST"])
def change_password():
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (g.user["id"],)).fetchone()
    if not check_password_hash(user["password_hash"], request.form.get("current", "")):
        flash("Mevcut şifre hatalı.", "error")
    elif len(request.form.get("new", "")) < 8:
        flash("Yeni şifre en az 8 karakter olmalı.", "error")
    elif request.form.get("new") != request.form.get("new2"):
        flash("Yeni şifreler eşleşmiyor.", "error")
    else:
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (generate_password_hash(request.form["new"]), user["id"]))
        db.commit()
        flash("Şifreniz değiştirildi.", "success")
    return redirect(url_for("settings.index") + "#hesap")


@bp.route("/ayarlar/kullanici", methods=["POST"])
def add_user():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{3,32}", username):
        flash("Kullanıcı adı 3-32 karakter olmalı (harf, rakam, . _ -).", "error")
    elif len(password) < 8:
        flash("Şifre en az 8 karakter olmalı.", "error")
    else:
        db = get_db()
        if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            flash("Bu kullanıcı adı zaten var.", "error")
        else:
            db.execute("INSERT INTO users(username, full_name, password_hash) VALUES (?, ?, ?)",
                       (username, request.form.get("full_name", "").strip(), generate_password_hash(password)))
            db.commit()
            flash(f"{username} kullanıcısı eklendi.", "success")
    return redirect(url_for("settings.index") + "#hesap")


@bp.route("/ayarlar/kullanici/<int:uid>/sil", methods=["POST"])
def delete_user(uid):
    if uid == g.user["id"]:
        flash("Kendi hesabınızı silemezsiniz.", "error")
    else:
        db = get_db()
        db.execute("DELETE FROM users WHERE id = ?", (uid,))
        db.commit()
        flash("Kullanıcı silindi.", "success")
    return redirect(url_for("settings.index") + "#hesap")


# ------------------------------------------------------------ Yedekler

@bp.route("/ayarlar/yedek", methods=["POST"])
def make_backup():
    limit = get_settings()["backup_limit_mb"]
    target, removed = backup.create_backup(
        current_app.config["DATABASE"], current_app.config["BACKUP_DIR"], limit, label="elle"
    )
    msg = f"Yedek alındı: {target.name}"
    if removed:
        msg += f" ({len(removed)} eski yedek sınır nedeniyle silindi)"
    flash(msg, "success")
    return redirect(url_for("settings.index") + "#yedekler")


@bp.route("/ayarlar/yedek/<name>")
def download_backup(name):
    if not re.fullmatch(r"odeme-[\w\-]+\.db\.gz", name):
        abort(404)
    path = Path(current_app.config["BACKUP_DIR"]) / name
    if not path.is_file():
        abort(404)
    return send_from_directory(current_app.config["BACKUP_DIR"], name, as_attachment=True)
