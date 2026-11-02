from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .db import get_db

bp = Blueprint("notes", __name__)


@bp.route("/notlar")
def index():
    show = request.args.get("goster", "acik")
    sql = """SELECT n.*, COALESCE(NULLIF(u.full_name, ''), u.username, 'Silinmiş kullanıcı') author
             FROM notes n LEFT JOIN users u ON u.id = n.user_id"""
    if show == "acik":
        sql += " WHERE n.done = 0"
    elif show == "tamam":
        sql += " WHERE n.done = 1"
    sql += " ORDER BY n.pinned DESC, n.created_at DESC, n.id DESC LIMIT 500"
    notes = get_db().execute(sql).fetchall()
    return render_template("notes.html", notes=notes, show=show)


def _back():
    return redirect(request.referrer if request.referrer and "/notlar" in request.referrer else url_for("notes.index"))


@bp.route("/notlar", methods=["POST"])
def create():
    body = request.form.get("body", "").strip()
    if not body:
        flash("Not boş olamaz.", "error")
    else:
        db = get_db()
        db.execute("INSERT INTO notes(user_id, body, pinned) VALUES (?, ?, ?)",
                   (g.user["id"], body[:5000], 1 if request.form.get("pinned") else 0))
        db.commit()
        flash("Not eklendi.", "success")
    return _back()


@bp.route("/notlar/<int:nid>/duzenle", methods=["POST"])
def edit(nid):
    body = request.form.get("body", "").strip()
    if body:
        db = get_db()
        db.execute("UPDATE notes SET body = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
                   (body[:5000], nid))
        db.commit()
        flash("Not güncellendi.", "success")
    return _back()


@bp.route("/notlar/<int:nid>/<action>", methods=["POST"])
def toggle(nid, action):
    db = get_db()
    if action == "sabitle":
        db.execute("UPDATE notes SET pinned = 1 - pinned WHERE id = ?", (nid,))
    elif action == "tamamla":
        db.execute("UPDATE notes SET done = 1 - done WHERE id = ?", (nid,))
    elif action == "sil":
        db.execute("DELETE FROM notes WHERE id = ?", (nid,))
        flash("Not silindi.", "success")
    db.commit()
    return _back()
