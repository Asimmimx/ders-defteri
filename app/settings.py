import re
from datetime import date
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, send_from_directory, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import backup, gcal, holidays, notify
from .audit import log
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
        token_hint=("…" + s["telegram_token"][-4:]) if s["telegram_token"] else "",
        special_days=db.execute("SELECT * FROM special_days ORDER BY start_date DESC LIMIT 100").fetchall(),
        upcoming=holidays.upcoming(db, date.today(), 120),
        gcal_status=gcal.status_text(s), gcal_connected=gcal.is_connected(s), gcal_configured=gcal.is_configured(s),
        gcal_redirect=url_for("gcal.callback", _external=True),
        portal_count=db.execute("SELECT COUNT(*) FROM portal_users WHERE active = 1").fetchone()[0],
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
    log("Komisyon ayarı değişti", commission_label(ctype, value), "settings")
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
    log("Program ayarı değişti", f"{start}:00–{end}:00, {slot} dk", "settings")
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
        log("Mesaj taslağı güncellendi", name, "template", tid)
    else:
        cur = db.execute("INSERT INTO message_templates(name, body) VALUES (?, ?)", (name, body))
        log("Mesaj taslağı eklendi", name, "template", cur.lastrowid)
    db.commit()
    flash("Taslak kaydedildi.", "success")
    return redirect(url_for("settings.index") + "#taslaklar")


@bp.route("/ayarlar/taslak/<int:tid>/sil", methods=["POST"])
def delete_template(tid):
    db = get_db()
    db.execute("DELETE FROM message_templates WHERE id = ?", (tid,))
    log("Mesaj taslağı silindi", f"#{tid}", "template", tid)
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
        log("Şifre değiştirildi", user["username"], "user", user["id"])
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
        from .auth import username_taken
        if username_taken(db, username):
            flash("Bu kullanıcı adı zaten var (yönetici veya veli/öğrenci hesabı).", "error")
        else:
            db.execute("INSERT INTO users(username, full_name, password_hash) VALUES (?, ?, ?)",
                       (username, request.form.get("full_name", "").strip(), generate_password_hash(password)))
            log("Kullanıcı eklendi", username, "user")
            db.commit()
            flash(f"{username} kullanıcısı eklendi.", "success")
    return redirect(url_for("settings.index") + "#hesap")


@bp.route("/ayarlar/kullanici/<int:uid>/sil", methods=["POST"])
def delete_user(uid):
    if uid == g.user["id"]:
        flash("Kendi hesabınızı silemezsiniz.", "error")
    else:
        db = get_db()
        row = db.execute("SELECT username FROM users WHERE id = ?", (uid,)).fetchone()
        db.execute("DELETE FROM users WHERE id = ?", (uid,))
        log("Kullanıcı silindi", row["username"] if row else f"#{uid}", "user", uid)
        db.commit()
        flash("Kullanıcı silindi.", "success")
    return redirect(url_for("settings.index") + "#hesap")


# ------------------------------------------------------------ Yedekler

@bp.route("/ayarlar/yedek", methods=["POST"])
def make_backup():
    limit = get_settings()["backup_limit_mb"]
    target, removed = backup.create_backup(
        current_app.config["DATABASE"], current_app.config["BACKUP_DIR"], limit, label="elle",
        upload_dir=current_app.config["UPLOAD_DIR"],
    )
    log("Elle yedek alındı", target.name, "backup")
    get_db().commit()
    msg = f"Yedek alındı: {target.name}"
    if removed:
        msg += f" ({len(removed)} eski yedek sınır nedeniyle silindi)"
    flash(msg, "success")
    return redirect(url_for("settings.index") + "#yedekler")


@bp.route("/ayarlar/yedek/<name>")
def download_backup(name):
    if not re.fullmatch(backup.NAME_RE, name):
        abort(404)
    path = Path(current_app.config["BACKUP_DIR"]) / name
    if not path.is_file():
        abort(404)
    return send_from_directory(current_app.config["BACKUP_DIR"], name, as_attachment=True)


# ------------------------------------------------------------ Telegram bildirimleri

@bp.route("/ayarlar/portal", methods=["POST"])
def save_portal():
    url = request.form.get("portal_url", "").strip().rstrip("/")
    if not re.fullmatch(r"https?://[\w.\-]+(:\d+)?(/[\w.\-/]*)?", url):
        flash("Geçerli bir adres girin (ör. https://portal.eneskayar.com).", "error")
        return redirect(url_for("settings.index") + "#portal")
    set_setting("portal_url", url)
    log("Portal adresi değişti", url, "settings")
    get_db().commit()
    flash("Portal adresi kaydedildi.", "success")
    return redirect(url_for("settings.index") + "#portal")


@bp.route("/ayarlar/telegram", methods=["POST"])
def save_telegram():
    f = request.form
    token = f.get("telegram_token", "").strip()
    chat_id = f.get("telegram_chat_id", "").strip()
    if token and not re.fullmatch(r"\d{5,}:[A-Za-z0-9_\-]{20,}", token):
        flash("Bot anahtarı (token) geçersiz görünüyor. BotFather'ın verdiği 123456:ABC... biçiminde olmalı.", "error")
        return redirect(url_for("settings.index") + "#telegram")
    if chat_id and not re.fullmatch(r"-?\d{3,20}", chat_id):
        flash("Chat ID sadece rakamlardan oluşmalı.", "error")
        return redirect(url_for("settings.index") + "#telegram")
    if token:  # boş bırakılırsa mevcut anahtar korunur
        set_setting("telegram_token", token)
    set_setting("telegram_chat_id", chat_id)
    set_setting("telegram_enabled", "1" if f.get("telegram_enabled") else "0")
    log("Telegram ayarı değişti", "açık" if f.get("telegram_enabled") else "kapalı", "settings")
    get_db().commit()
    flash("Telegram ayarları kaydedildi.", "success")
    return redirect(url_for("settings.index") + "#telegram")


@bp.route("/ayarlar/telegram/chat-id", methods=["POST"])
def telegram_find_chat():
    s = get_settings()
    if not s["telegram_token"]:
        flash("Önce bot anahtarını kaydedin.", "error")
        return redirect(url_for("settings.index") + "#telegram")
    chat_id, info = notify.find_chat_id(s["telegram_token"])
    if chat_id:
        set_setting("telegram_chat_id", chat_id)
        get_db().commit()
        flash(f"Chat ID bulundu ve kaydedildi: {chat_id} ({info})", "success")
    else:
        flash(info, "error")
    return redirect(url_for("settings.index") + "#telegram")


@bp.route("/ayarlar/telegram/test", methods=["POST"])
def telegram_test():
    s = get_settings()
    if not (s["telegram_token"] and s["telegram_chat_id"]):
        flash("Bot anahtarı ve Chat ID gerekli.", "error")
        return redirect(url_for("settings.index") + "#telegram")
    text = notify.daily_summary(get_db()) or "✅ Ödeme Takip bağlantısı çalışıyor. Şu an bildirilecek ödeme yok."
    ok, err = notify.send_message(s["telegram_token"], s["telegram_chat_id"], text)
    flash("Test mesajı gönderildi, Telegram'ı kontrol edin." if ok else f"Gönderilemedi: {err}",
          "success" if ok else "error")
    return redirect(url_for("settings.index") + "#telegram")


# ------------------------------------------------------------ İşlem geçmişi

@bp.route("/ayarlar/gecmis")
def audit():
    db = get_db()
    q = request.args.get("q", "").strip()
    uid = parse_int(request.args.get("kullanici"), 0)
    page = max(parse_int(request.args.get("sayfa"), 1), 1)
    per = 100
    where, args = " WHERE 1 = 1", []
    if q:
        where += " AND (action LIKE ? OR detail LIKE ?)"
        args += [f"%{q}%"] * 2
    if uid:
        where += " AND user_id = ?"
        args.append(uid)
    total = db.execute("SELECT COUNT(*) FROM audit_log" + where, args).fetchone()[0]
    rows = db.execute("SELECT * FROM audit_log" + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
                      (*args, per, (page - 1) * per)).fetchall()
    users = db.execute("SELECT id, username, full_name FROM users ORDER BY id").fetchall()
    return render_template("audit.html", rows=rows, q=q, uid=uid, page=page,
                           pages=max((total + per - 1) // per, 1), total=total, users=users)


# ------------------------------------------------------------ Özel günler

@bp.route("/ayarlar/ozel-gun", methods=["POST"])
def add_special_day():
    name = request.form.get("name", "").strip()[:80]
    start = request.form.get("start_date", "")
    end = request.form.get("end_date", "") or start
    try:
        d1, d2 = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        flash("Geçerli bir tarih seçin.", "error")
        return redirect(url_for("settings.index") + "#ozel-gunler")
    if not name:
        flash("Özel günün adını yazın.", "error")
        return redirect(url_for("settings.index") + "#ozel-gunler")
    if d2 < d1:
        d1, d2 = d2, d1
    if (d2 - d1).days > 366:
        flash("En fazla 1 yıllık aralık girilebilir.", "error")
        return redirect(url_for("settings.index") + "#ozel-gunler")
    db = get_db()
    cur = db.execute("INSERT INTO special_days(name, start_date, end_date) VALUES (?, ?, ?)",
                     (name, d1.isoformat(), d2.isoformat()))
    log("Özel gün eklendi", f"{name}: {d1.isoformat()} – {d2.isoformat()}", "special_day", cur.lastrowid)
    db.commit()
    flash(f"{name} eklendi; programda görünecek.", "success")
    return redirect(url_for("settings.index") + "#ozel-gunler")


@bp.route("/ayarlar/ozel-gun/<int:sid>/sil", methods=["POST"])
def delete_special_day(sid):
    db = get_db()
    row = db.execute("SELECT * FROM special_days WHERE id = ?", (sid,)).fetchone()
    db.execute("DELETE FROM special_days WHERE id = ?", (sid,))
    if row:
        log("Özel gün silindi", f"{row['name']}: {row['start_date']} – {row['end_date']}", "special_day", sid)
    db.commit()
    flash("Özel gün silindi.", "success")
    return redirect(url_for("settings.index") + "#ozel-gunler")
