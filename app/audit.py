"""İşlem geçmişi: kim, ne zaman, ne yaptı.

log() sadece kaydı ekler; commit'i çağıran yapar (aynı işlemle birlikte kaydedilsin diye).
"""
from flask import g, has_request_context, request

from .db import get_db

KEEP_ROWS = 20000


def log(action, detail="", entity=None, entity_id=None, user=None, db=None):
    db = db or get_db()
    user = user if user is not None else (g.get("user") if has_request_context() else None)
    ip = ""
    if has_request_context():
        ip = request.headers.get("CF-Connecting-IP") or request.remote_addr or ""
    db.execute(
        """INSERT INTO audit_log(user_id, username, action, detail, entity, entity_id, ip)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            user["id"] if user else None,
            (user["full_name"] or user["username"]) if user else "",
            action, str(detail or "")[:1000], entity, entity_id, ip[:64],
        ),
    )
    # Ara sıra eski kayıtları temizle
    row = db.execute("SELECT MAX(id) FROM audit_log").fetchone()
    if row and row[0] and row[0] % 500 == 0:
        db.execute("DELETE FROM audit_log WHERE id <= ?", (row[0] - KEEP_ROWS,))
