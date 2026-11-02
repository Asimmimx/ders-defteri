"""Google Takvim çift yönlü senkron (TASLAK — kurulum tamamlanınca çalışır).

Kurulum: Google Cloud Console → OAuth istemcisi (Web uygulaması) → istemci kimliği ve gizli anahtar
Ayarlar → Google Takvim'e girilir → "Google ile bağlan" (Enes Hoca'nın hesabı) → refresh token kaydedilir.
Bağlantı yoksa hiçbir şey yapılmaz; program her zamanki gibi çalışır.

Senkron (manage.py gcal-sync, systemd timer ile 5 dakikada bir):
  1) Buradan Google'a: değişen dersler (gcal_synced = 0) etkinlik olarak yazılır/güncellenir,
     silinen derslerin etkinlikleri (gcal_trash) Google'dan silinir.
  2) Google'dan buraya: Enes Hoca'nın takvimindeki (bizim yazmadığımız) etkinlikler,
     programda "Dolu" (busy_blocks.source = 'gcal') olarak gösterilir. Elle girilen dolu saatlere ve derslere dokunulmaz.
"""
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, abort, flash, redirect, request, session, url_for

from .audit import log
from .db import DEFAULT_SETTINGS, get_db, set_setting

bp = Blueprint("gcal", __name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar.events"
TZ_NAME = "Europe/Istanbul"
TZ = timezone(timedelta(hours=3))  # Türkiye 2016'dan beri sabit UTC+3
PROP = "odemetakip_slot"
WEEKS_BACK, WEEKS_AHEAD = 1, 8

_token_cache = {}


class GcalError(Exception):
    pass


def _settings(db):
    s = dict(DEFAULT_SETTINGS)
    s.update({r[0]: r[1] for r in db.execute("SELECT key, value FROM settings")})
    return s


def _put_setting(db, key, value):
    db.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               (key, str(value)))


def is_configured(s):
    return bool(s["gcal_client_id"] and s["gcal_client_secret"])


def is_connected(s):
    return is_configured(s) and bool(s["gcal_refresh_token"])


# ---------------------------------------------------------------- HTTP

def _http(method, url, data=None, token=None, form=False):
    headers = {"Accept": "application/json"}
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
        except ValueError:
            payload = {}
        return e.code, payload
    except (urllib.error.URLError, TimeoutError) as e:
        raise GcalError(f"Google'a bağlanılamadı: {e}") from e


def _access_token(s):
    cached = _token_cache.get(s["gcal_refresh_token"])
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    status, data = _http("POST", TOKEN_URL, {
        "client_id": s["gcal_client_id"], "client_secret": s["gcal_client_secret"],
        "refresh_token": s["gcal_refresh_token"], "grant_type": "refresh_token",
    }, form=True)
    if status != 200 or "access_token" not in data:
        raise GcalError(f"Google oturumu yenilenemedi ({data.get('error', status)}). Yeniden bağlanın.")
    _token_cache[s["gcal_refresh_token"]] = (data["access_token"], time.time() + int(data.get("expires_in", 3600)))
    return data["access_token"]


# ---------------------------------------------------------------- senkron

def _window():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return monday - timedelta(weeks=WEEKS_BACK), monday + timedelta(weeks=WEEKS_AHEAD)


def _event_body(slot, slot_minutes):
    d = date.fromisoformat(slot["week_start"]) + timedelta(days=slot["day"])
    start = datetime(d.year, d.month, d.day) + timedelta(minutes=slot["start_min"])
    end = start + timedelta(minutes=slot_minutes)
    kind = "online" if slot["lesson_type"] == "online" else "yüz yüze"
    return {
        "summary": f"Ders: {slot['student_name']} ({kind})",
        "description": (slot["note"] or "") + "\n\nÖdeme Takip programından otomatik eklendi.",
        "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": TZ_NAME},
        "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": TZ_NAME},
        "extendedProperties": {"private": {PROP: str(slot["id"])}},
    }


def _push(db, s, token, cal):
    slot_minutes = int(s["slot_minutes"] or 60)
    base = f"{API}/calendars/{urllib.parse.quote(cal, safe='')}/events"
    pushed = deleted = 0
    for r in db.execute("SELECT event_id FROM gcal_trash").fetchall():
        status, _ = _http("DELETE", f"{base}/{urllib.parse.quote(r['event_id'], safe='')}", token=token)
        if status in (200, 204, 404, 410):
            db.execute("DELETE FROM gcal_trash WHERE event_id = ?", (r["event_id"],))
            deleted += 1
        else:
            raise GcalError(f"Google etkinliği silinemedi (HTTP {status}).")
    start, end = _window()
    rows = db.execute(
        """SELECT sl.*, st.name student_name FROM schedule_slots sl JOIN students st ON st.id = sl.student_id
           WHERE sl.gcal_synced = 0 AND sl.week_start >= ? AND sl.week_start < ?""",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    for slot in rows:
        body = _event_body(slot, slot_minutes)
        status = None
        if slot["gcal_event_id"]:
            status, data = _http("PUT", f"{base}/{urllib.parse.quote(slot['gcal_event_id'], safe='')}", body, token)
        if status is None or status in (404, 410):
            status, data = _http("POST", base, body, token)
        if status not in (200, 201):
            raise GcalError(f"Ders Google'a yazılamadı (HTTP {status}: {data.get('error', {}).get('message', '')}).")
        db.execute("UPDATE schedule_slots SET gcal_event_id = ?, gcal_synced = 1 WHERE id = ?", (data["id"], slot["id"]))
        pushed += 1
    return pushed, deleted


def _cells(ev_start, ev_end, grid_start, grid_end, slot_minutes):
    """Etkinliğin kapladığı program hücreleri: [(week_start, day, start_min)]"""
    out = []
    d = ev_start.date()
    while d <= ev_end.date():
        day_start = datetime(d.year, d.month, d.day, tzinfo=TZ)
        for m in range(grid_start * 60, grid_end * 60, slot_minutes):
            c0 = day_start + timedelta(minutes=m)
            c1 = c0 + timedelta(minutes=slot_minutes)
            if c0 < ev_end and c1 > ev_start:
                monday = d - timedelta(days=d.weekday())
                out.append((monday.isoformat(), d.weekday(), m))
        d += timedelta(days=1)
    return out


def _pull(db, s, token, cal):
    start, end = _window()
    params = {
        "timeMin": datetime(start.year, start.month, start.day, tzinfo=TZ).isoformat(),
        "timeMax": datetime(end.year, end.month, end.day, tzinfo=TZ).isoformat(),
        "singleEvents": "true", "orderBy": "startTime", "maxResults": "2500", "timeZone": TZ_NAME,
    }
    url = f"{API}/calendars/{urllib.parse.quote(cal, safe='')}/events?{urllib.parse.urlencode(params)}"
    status, data = _http("GET", url, token=token)
    if status != 200:
        raise GcalError(f"Google takvimi okunamadı (HTTP {status}).")
    grid_start, grid_end = int(s["schedule_start_hour"]), int(s["schedule_end_hour"])
    slot_minutes = int(s["slot_minutes"] or 60)
    wanted = {}
    for ev in data.get("items", []):
        if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
            continue
        if PROP in (ev.get("extendedProperties", {}).get("private") or {}):
            continue  # bizim yazdığımız ders
        st, en = ev.get("start", {}).get("dateTime"), ev.get("end", {}).get("dateTime")
        if not st or not en:
            continue  # tüm gün etkinlikleri (doğum günü, tatil vb.) atlanır
        ev_start = datetime.fromisoformat(st.replace("Z", "+00:00")).astimezone(TZ)
        ev_end = datetime.fromisoformat(en.replace("Z", "+00:00")).astimezone(TZ)
        label = ("Google: " + (ev.get("summary") or "Meşgul"))[:60]
        for cell in _cells(ev_start, ev_end, grid_start, grid_end, slot_minutes):
            wanted.setdefault(cell, (label, ev["id"]))

    existing = {(r["week_start"], r["day"], r["start_min"]): r for r in db.execute(
        "SELECT * FROM busy_blocks WHERE source = 'gcal' AND week_start >= ? AND week_start < ?",
        (start.isoformat(), end.isoformat()))}
    added = removed = 0
    for key, row in existing.items():
        if key not in wanted:
            db.execute("DELETE FROM busy_blocks WHERE id = ?", (row["id"],))
            removed += 1
        elif (row["label"], row["gcal_event_id"]) != wanted[key]:
            db.execute("UPDATE busy_blocks SET label = ?, gcal_event_id = ? WHERE id = ?", (*wanted[key], row["id"]))
    for key, (label, event_id) in wanted.items():
        if key in existing:
            continue
        week, day, m = key
        taken = db.execute(
            "SELECT 1 FROM schedule_slots WHERE week_start = ? AND day = ? AND start_min = ? UNION ALL "
            "SELECT 1 FROM busy_blocks WHERE week_start = ? AND day = ? AND start_min = ?", (week, day, m) * 2,
        ).fetchone()
        if taken:
            continue
        db.execute("INSERT INTO busy_blocks(week_start, day, start_min, label, source, gcal_event_id) "
                   "VALUES (?, ?, ?, ?, 'gcal', ?)", (week, day, m, label, event_id))
        added += 1
    return added, removed


def sync(db):
    """Tek senkron turu. (özet metni) döner; bağlı değilse None. Hata olursa GcalError."""
    s = _settings(db)
    if not is_connected(s):
        return None
    cal = s["gcal_calendar_id"] or "primary"
    try:
        token = _access_token(s)
        pushed, deleted = _push(db, s, token, cal)
        added, removed = _pull(db, s, token, cal)
    except GcalError as e:
        db.rollback()
        _put_setting(db, "gcal_last_error", f"{datetime.now():%d.%m.%Y %H:%M} · {e}")
        db.commit()
        raise
    summary = f"{pushed} ders gönderildi, {deleted} silindi; Google'dan {added} dolu saat eklendi, {removed} kaldırıldı"
    if pushed or deleted or added or removed:
        log("Google Takvim senkronu", summary, "gcal", user=None, db=db)
    _put_setting(db, "gcal_last_sync", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    _put_setting(db, "gcal_last_error", "")
    db.commit()
    return summary


# ---------------------------------------------------------------- ayar sayfası uçları

def _back():
    return redirect(url_for("settings.index") + "#google")


@bp.route("/ayarlar/google", methods=["POST"])
def save():
    f = request.form
    cid = f.get("gcal_client_id", "").strip()
    secret = f.get("gcal_client_secret", "").strip()
    cal = f.get("gcal_calendar_id", "").strip() or "primary"
    if cid and not cid.endswith(".apps.googleusercontent.com"):
        flash("İstemci kimliği ...apps.googleusercontent.com ile bitmeli.", "error")
        return _back()
    set_setting("gcal_client_id", cid)
    if secret:
        set_setting("gcal_client_secret", secret)
    set_setting("gcal_calendar_id", cal[:200])
    log("Google Takvim ayarı değişti", f"takvim: {cal}", "settings")
    get_db().commit()
    flash("Google Takvim ayarları kaydedildi.", "success")
    return _back()


@bp.route("/google/baglan")
def connect():
    s = _settings(get_db())
    if not is_configured(s):
        flash("Önce istemci kimliği ve gizli anahtarı kaydedin.", "error")
        return _back()
    state = secrets.token_urlsafe(24)
    session["gcal_state"] = state
    params = {
        "client_id": s["gcal_client_id"], "redirect_uri": url_for("gcal.callback", _external=True),
        "response_type": "code", "scope": SCOPE, "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true", "state": state,
    }
    return redirect(f"{AUTH_URL}?{urllib.parse.urlencode(params)}")


@bp.route("/google/geri")
def callback():
    if not request.args.get("state") or request.args.get("state") != session.pop("gcal_state", None):
        abort(400, "Google bağlantısı doğrulanamadı. Tekrar deneyin.")
    if request.args.get("error"):
        flash(f"Google bağlantısı iptal edildi ({request.args['error']}).", "error")
        return _back()
    db = get_db()
    s = _settings(db)
    try:
        status, data = _http("POST", TOKEN_URL, {
            "code": request.args.get("code", ""), "client_id": s["gcal_client_id"],
            "client_secret": s["gcal_client_secret"], "redirect_uri": url_for("gcal.callback", _external=True),
            "grant_type": "authorization_code",
        }, form=True)
    except GcalError as e:
        flash(str(e), "error")
        return _back()
    if status != 200 or not data.get("refresh_token"):
        flash(f"Google bağlantısı tamamlanamadı ({data.get('error_description') or data.get('error') or status}).", "error")
        return _back()
    set_setting("gcal_refresh_token", data["refresh_token"])
    db.execute("UPDATE schedule_slots SET gcal_synced = 0")  # mevcut dersler ilk senkronda gönderilsin
    log("Google Takvim bağlandı", s["gcal_calendar_id"], "gcal")
    db.commit()
    flash("Google Takvim bağlandı. İlk senkron için “Şimdi senkronize et”e basın.", "success")
    return _back()


@bp.route("/google/senkron", methods=["POST"])
def sync_now():
    db = get_db()
    try:
        summary = sync(db)
    except GcalError as e:
        flash(str(e), "error")
        return _back()
    flash(summary or "Google Takvim bağlı değil.", "success" if summary else "error")
    return _back()


@bp.route("/google/kes", methods=["POST"])
def disconnect():
    db = get_db()
    set_setting("gcal_refresh_token", "")
    n = db.execute("DELETE FROM busy_blocks WHERE source = 'gcal'").rowcount
    db.execute("UPDATE schedule_slots SET gcal_event_id = NULL, gcal_synced = 0")
    db.execute("DELETE FROM gcal_trash")
    log("Google Takvim bağlantısı kesildi", f"Google'dan gelen {n} dolu saat kaldırıldı", "gcal")
    db.commit()
    flash("Google Takvim bağlantısı kesildi. (Google'daki mevcut etkinliklere dokunulmadı.)", "success")
    return _back()


def status_text(s):
    if not is_configured(s):
        return "Kurulum bekleniyor"
    if not s["gcal_refresh_token"]:
        return "İstemci bilgileri girildi · Google hesabı bağlanmadı"
    return "Bağlı" + (f" · son senkron {s['gcal_last_sync'][:16]}" if s["gcal_last_sync"] else "")

