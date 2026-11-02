import re
from datetime import date, datetime

from jinja2 import Undefined

DAYS = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
          "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
METHODS = ["Nakit", "Havale/EFT", "Kredi kartı", "Diğer"]


def money(value):
    """1250.5 -> '1.250,50 ₺'"""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if s.endswith(",00"):
        s = s[:-3]
    return f"{'-' if v < 0 else ''}{s} ₺"


def num(value):
    """Form alanları için: 1250.0 -> '1250', 1250.5 -> '1250,50'"""
    if isinstance(value, Undefined) or value is None or value == "":
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return str(int(f))
    return f"{f:.2f}".replace(".", ",")


def parse_money(text, default=0.0):
    """'1.250,50', '1250.50', '1250' -> float"""
    if text is None:
        return default
    s = str(text).strip().replace("₺", "").replace("TL", "").replace(" ", "")
    if not s:
        return default
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1 or re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return round(float(s), 2)
    except ValueError:
        return default


def parse_hours(text, default=0.0):
    """'1,5' / '1.5' / '2' -> saat (float, en fazla 2 ondalık)"""
    s = str(text or "").strip().replace(",", ".")
    if not s:
        return default
    try:
        v = round(float(s), 2)
    except ValueError:
        return default
    return v if v >= 0 else default


def qty(value):
    """1.5 -> '1,5', 2.0 -> '2'"""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return str(value)
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def money_text(value):
    """SMS için: 3200 -> '3.200 TL'"""
    return money(value).replace(" ₺", " TL")


def parse_int(text, default=0):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return default


def fmt_date(value):
    if not value:
        return "—"
    try:
        d = datetime.fromisoformat(str(value)[:19])
    except ValueError:
        return str(value)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def fmt_datetime(value):
    if not value:
        return "—"
    try:
        d = datetime.fromisoformat(str(value)[:19])
    except ValueError:
        return str(value)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year} {d:%H:%M}"


def today_iso():
    return date.today().isoformat()


def minutes_to_hhmm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def normalize_phone(phone):
    """SMS/WhatsApp linki için +90 formatı."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = "90" + digits[1:]
    elif len(digits) == 10:
        digits = "90" + digits
    return "+" + digits


def compute_commission(amount, total_lessons, ctype, cvalue):
    cvalue = float(cvalue or 0)
    if ctype == "fixed":
        return round(cvalue * float(total_lessons or 0), 2)
    return round(float(amount or 0) * cvalue / 100.0, 2)


def commission_label(ctype, cvalue):
    if ctype == "fixed":
        return f"Saat başı {money(cvalue)}"
    v = float(cvalue or 0)
    return f"%{v:g}"
