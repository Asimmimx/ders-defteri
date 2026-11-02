"""Telegram bildirimleri (ek bağımlılık yok, urllib ile)."""
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from .utils import fmt_date, money_text

API = "https://api.telegram.org/bot{token}/{method}"


def _call(token, method, params=None, timeout=15):
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(API.format(token=token, method=method), data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return False, f"HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return False, f"Bağlantı hatası: {e}"
    if not body.get("ok"):
        return False, body.get("description", "Bilinmeyen hata")
    return True, body.get("result")


def send_message(token, chat_id, text):
    ok, res = _call(token, "sendMessage", {"chat_id": chat_id, "text": text[:4000],
                                           "disable_web_page_preview": "true"})
    return ok, (None if ok else res)


def find_chat_id(token):
    """Bota en son mesaj atan sohbetin kimliği."""
    ok, res = _call(token, "getUpdates", {"limit": 50})
    if not ok:
        return None, res
    for upd in reversed(res or []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
            return str(chat["id"]), name or chat.get("username") or ""
    return None, "Bota henüz mesaj gönderilmemiş. Telegram'da botunuza bir mesaj (ör. /start) atıp tekrar deneyin."


def _row_text(r):
    return f"• {r['parent_name']} ({r['student_name']}) — {money_text(r['amount'])}"


def daily_summary(conn, today=None):
    """Bugün/yarın son günü olan ve geciken ödemelerin özeti. Bildirilecek bir şey yoksa None."""
    today = today or date.today()
    t, tomorrow = today.isoformat(), (today + timedelta(days=1)).isoformat()
    q = """SELECT p.amount, p.due_date, s.name student_name, pa.name parent_name
           FROM payments p JOIN students s ON s.id = p.student_id JOIN parents pa ON pa.id = s.parent_id
           WHERE p.status = 'pending' AND p.due_date {cond} ORDER BY p.due_date, pa.name"""
    due_today = conn.execute(q.format(cond="= ?"), (t,)).fetchall()
    due_tomorrow = conn.execute(q.format(cond="= ?"), (tomorrow,)).fetchall()
    overdue = conn.execute(q.format(cond="< ?"), (t,)).fetchall()
    if not (due_today or due_tomorrow or overdue):
        return None
    lines = [f"📋 Ödeme özeti — {fmt_date(t)}"]
    if due_today:
        lines += ["", f"Bugün son gün ({len(due_today)}):"] + [_row_text(r) for r in due_today]
    if due_tomorrow:
        lines += ["", f"Yarın son gün ({len(due_tomorrow)}):"] + [_row_text(r) for r in due_tomorrow]
    if overdue:
        total = sum(r["amount"] for r in overdue)
        lines += ["", f"⚠️ Geciken ({len(overdue)}, toplam {money_text(total)}):"]
        lines += [f"{_row_text(r)} · {fmt_date(r['due_date'])}" for r in overdue[:15]]
        if len(overdue) > 15:
            lines.append(f"… ve {len(overdue) - 15} ödeme daha")
    return "\n".join(lines)
