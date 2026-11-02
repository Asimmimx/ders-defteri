"""Personel tarafı: veli / öğrenci portal hesaplarını yönetme (oluştur, şifre sıfırla, aç/kapat, sil)."""
import re
import secrets

from flask import Blueprint, abort, flash, redirect, request, session, url_for
from werkzeug.security import generate_password_hash

from .audit import log
from .auth import username_taken
from .db import PORTAL_TEMPLATE, get_db, get_settings

bp = Blueprint("access", __name__)

_TR = str.maketrans("çğıöşüÇĞİÖŞÜâîû", "cgiosuCGIOSUaiu")
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # karışabilecek harfler (l, 1, o, 0, i) yok


def new_password():
    return "".join(secrets.choice(_ALPHABET) for _ in range(4)) + "-" + "".join(
        secrets.choice(_ALPHABET) for _ in range(4))


def suggest_username(db, name):
    base = re.sub(r"[^a-z0-9]+", ".", name.translate(_TR).lower()).strip(".")[:24] or "kullanici"
    candidate, n = base, 2
    while username_taken(db, candidate):
        candidate, n = f"{base}{n}", n + 1
    return candidate


def accounts_for_parent(db, pid):
    """{'parent': row|None, 'students': {student_id: row}}"""
    out = {"parent": None, "students": {}}
    for r in db.execute(
        """SELECT * FROM portal_users WHERE parent_id = ?
           OR student_id IN (SELECT id FROM students WHERE parent_id = ?)""", (pid, pid)
    ).fetchall():
        if r["role"] == "parent":
            out["parent"] = r
        else:
            out["students"][r["student_id"]] = r
    return out


def credential_message(name, username, password, role="parent"):
    text = PORTAL_TEMPLATE.format(veli=name, portal_adresi=get_settings()["portal_url"],
                                  kullanici_adi=username, sifre=password)
    if role == "student":
        text = text.replace("Ders programını, derste işlenen konuları ve ödemeleri", "Ders programını ve derste işlenen konuları")
    return text


def _remember(pid, name, username, password, role="parent"):
    """Yeni şifre bir kez, veli sayfasında gösterilir (veritabanında sadece özeti tutulur)."""
    session["portal_cred"] = {"pid": pid, "name": name, "username": username,
                              "message": credential_message(name, username, password, role)}


def _owner(db, row):
    if row["role"] == "parent":
        p = db.execute("SELECT id, name FROM parents WHERE id = ?", (row["parent_id"],)).fetchone()
        return p["id"], p["name"], "veli"
    s = db.execute("SELECT parent_id, name FROM students WHERE id = ?", (row["student_id"],)).fetchone()
    return s["parent_id"], s["name"], "öğrenci"


@bp.route("/veliler/<int:pid>/portal", methods=["POST"])
def create(pid):
    db = get_db()
    parent = db.execute("SELECT * FROM parents WHERE id = ?", (pid,)).fetchone()
    if parent is None:
        abort(404)
    sid = request.form.get("student_id")
    if sid:
        student = db.execute("SELECT * FROM students WHERE id = ? AND parent_id = ?", (sid, pid)).fetchone()
        if student is None:
            abort(404)
        if db.execute("SELECT 1 FROM portal_users WHERE student_id = ?", (student["id"],)).fetchone():
            flash("Bu öğrencinin zaten portal hesabı var.", "error")
            return redirect(url_for("main.parent_detail", pid=pid) + "#portal")
        role, name, cols = "student", student["name"], (None, student["id"])
    else:
        if db.execute("SELECT 1 FROM portal_users WHERE parent_id = ? AND role = 'parent'", (pid,)).fetchone():
            flash("Bu velinin zaten portal hesabı var.", "error")
            return redirect(url_for("main.parent_detail", pid=pid) + "#portal")
        role, name, cols = "parent", parent["name"], (pid, None)

    username = request.form.get("username", "").strip().lower()
    if username:
        if not re.fullmatch(r"[a-z0-9_.\-]{3,32}", username):
            flash("Kullanıcı adı 3-32 karakter olmalı (küçük harf, rakam, . _ -).", "error")
            return redirect(url_for("main.parent_detail", pid=pid) + "#portal")
        if username_taken(db, username):
            flash("Bu kullanıcı adı alınmış.", "error")
            return redirect(url_for("main.parent_detail", pid=pid) + "#portal")
    else:
        username = suggest_username(db, name)
    password = new_password()
    cur = db.execute(
        "INSERT INTO portal_users(username, password_hash, role, parent_id, student_id) VALUES (?, ?, ?, ?, ?)",
        (username, generate_password_hash(password), role, *cols),
    )
    log("Portal hesabı oluşturuldu", f"{name} ({'veli' if role == 'parent' else 'öğrenci'}): {username}",
        "portal", cur.lastrowid)
    db.commit()
    _remember(pid, parent["name"] if role == "parent" else name, username, password, role)
    flash(f"{name} için portal hesabı oluşturuldu.", "success")
    return redirect(url_for("main.parent_detail", pid=pid) + "#portal")


def _get(uid):
    row = get_db().execute("SELECT * FROM portal_users WHERE id = ?", (uid,)).fetchone()
    if row is None:
        abort(404)
    return row


@bp.route("/portal-hesap/<int:uid>/sifirla", methods=["POST"])
def reset(uid):
    db = get_db()
    row = _get(uid)
    pid, name, kind = _owner(db, row)
    password = new_password()
    db.execute("UPDATE portal_users SET password_hash = ? WHERE id = ?", (generate_password_hash(password), uid))
    log("Portal şifresi sıfırlandı", f"{name} ({kind}): {row['username']}", "portal", uid)
    db.commit()
    _remember(pid, name, row["username"], password, row["role"])
    flash(f"{name} için yeni şifre oluşturuldu.", "success")
    return redirect(url_for("main.parent_detail", pid=pid) + "#portal")


@bp.route("/portal-hesap/<int:uid>/durum", methods=["POST"])
def toggle(uid):
    db = get_db()
    row = _get(uid)
    pid, name, kind = _owner(db, row)
    active = 0 if row["active"] else 1
    db.execute("UPDATE portal_users SET active = ? WHERE id = ?", (active, uid))
    log("Portal hesabı " + ("açıldı" if active else "kapatıldı"), f"{name} ({kind}): {row['username']}", "portal", uid)
    db.commit()
    flash(f"{name} portal erişimi {'açıldı' if active else 'kapatıldı'}.", "success")
    return redirect(url_for("main.parent_detail", pid=pid) + "#portal")


@bp.route("/portal-hesap/<int:uid>/sil", methods=["POST"])
def delete(uid):
    db = get_db()
    row = _get(uid)
    pid, name, kind = _owner(db, row)
    db.execute("DELETE FROM portal_users WHERE id = ?", (uid,))
    log("Portal hesabı silindi", f"{name} ({kind}): {row['username']}", "portal", uid)
    db.commit()
    flash(f"{name} portal hesabı silindi.", "success")
    return redirect(url_for("main.parent_detail", pid=pid) + "#portal")
