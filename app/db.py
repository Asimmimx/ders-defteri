import sqlite3
from pathlib import Path

from flask import current_app, g

DEFAULT_SETTINGS = {
    "commission_type": "percent",  # percent | fixed
    "commission_value": "20",       # yüzde ise %, sabit ise ders başı TL
    "schedule_start_hour": "9",
    "schedule_end_hour": "22",
    "slot_minutes": "60",
    "backup_limit_mb": "100",
}

PAYMENT_TEMPLATE = (
    "Merhaba {veli}\n"
    "Ödeme bilgisi için sizlere ulaşıyorum\n"
    "\n"
    "{ders_listesi}\n"
    "Toplam {tutar} ödeme göndermeniz uygundur\n"
    "Ödemeyi gönderdikten sonra dekont gönderirseniz sevinirim\n"
    "\n"
    "Teşekkür ederim🙏"
)

DEFAULT_TEMPLATES = [
    ("Ödeme bilgisi", PAYMENT_TEMPLATE),
    ("Kısa hatırlatma", "Merhaba {veli}, {ogrenci} ders ücreti: {tutar}. Teşekkürler."),
]

# Eski sürümün varsayılan taslağı (değiştirilmemişse yenisiyle güncellenir)
OLD_DEFAULT_BODY = (
    "Merhaba {veli}, {ogrenci} için {ders_sayisi} dersin ücreti olan {tutar} "
    "ödemesini hatırlatmak isteriz. Son ödeme tarihi: {son_odeme}. İyi günler dileriz."
)


def connect(path):
    conn = sqlite3.connect(path, detect_types=0, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))
    if conn.execute("SELECT COUNT(*) FROM message_templates").fetchone()[0] == 0:
        conn.executemany("INSERT INTO message_templates(name, body) VALUES (?, ?)", DEFAULT_TEMPLATES)
    _migrate(conn)
    conn.commit()
    conn.close()


def _migrate(conn):
    """Eski veritabanlarını güncel şemaya getirir."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(payments)")}
    if "online_unit" not in cols:
        conn.execute("ALTER TABLE payments ADD COLUMN online_unit REAL NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE payments ADD COLUMN inperson_unit REAL NOT NULL DEFAULT 0")
        conn.execute(
            """UPDATE payments SET
                 online_unit = (SELECT online_price FROM students s WHERE s.id = payments.student_id),
                 inperson_unit = (SELECT inperson_price FROM students s WHERE s.id = payments.student_id)"""
        )
    conn.execute(
        "UPDATE message_templates SET name = ?, body = ? WHERE body = ?",
        ("Ödeme bilgisi", PAYMENT_TEMPLATE, OLD_DEFAULT_BODY),
    )


def get_settings():
    rows = get_db().execute("SELECT key, value FROM settings").fetchall()
    data = dict(DEFAULT_SETTINGS)
    data.update({r["key"]: r["value"] for r in rows})
    return data


def set_setting(key, value):
    get_db().execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
