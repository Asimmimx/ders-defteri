"""Türkiye resmi tatilleri + kullanıcının eklediği özel günler.

Dini bayram tarihleri Diyanet takvimine göre elle girilmiştir (her yıl değişir).
2028 ve sonrası tahminidir; kesinleşince kontrol edilmeli. Yeni yıl eklemek için RELIGIOUS sözlüğüne satır ekleyin.
"""
from datetime import date, timedelta

# (ay, gün, ad, yarım_gün)
FIXED = [
    (1, 1, "Yılbaşı", False),
    (4, 23, "Ulusal Egemenlik ve Çocuk Bayramı", False),
    (5, 1, "Emek ve Dayanışma Günü", False),
    (5, 19, "Atatürk'ü Anma, Gençlik ve Spor Bayramı", False),
    (7, 15, "Demokrasi ve Millî Birlik Günü", False),
    (8, 30, "Zafer Bayramı", False),
    (10, 28, "Cumhuriyet Bayramı Arifesi", True),
    (10, 29, "Cumhuriyet Bayramı", False),
]

# yıl: (Ramazan Bayramı 1. gün, Kurban Bayramı 1. gün) — arife bir önceki gün (yarım gün)
RELIGIOUS = {
    2025: (date(2025, 3, 30), date(2025, 6, 6)),
    2026: (date(2026, 3, 20), date(2026, 5, 27)),
    2027: (date(2027, 3, 9), date(2027, 5, 16)),
    2028: (date(2028, 2, 26), date(2028, 5, 5)),   # tahmini
    2029: (date(2029, 2, 14), date(2029, 4, 24)),   # tahmini
    2030: (date(2030, 2, 4), date(2030, 4, 13)),    # tahmini
}


def official_holidays(year):
    """{date: (ad, yarım_gün)}"""
    out = {}
    for m, d, name, half in FIXED:
        out[date(year, m, d)] = (name, half)
    if year in RELIGIOUS:
        ramazan, kurban = RELIGIOUS[year]
        out[ramazan - timedelta(days=1)] = ("Ramazan Bayramı Arifesi", True)
        for i in range(3):
            out[ramazan + timedelta(days=i)] = (f"Ramazan Bayramı {i + 1}. gün", False)
        out[kurban - timedelta(days=1)] = ("Kurban Bayramı Arifesi", True)
        for i in range(4):
            out[kurban + timedelta(days=i)] = (f"Kurban Bayramı {i + 1}. gün", False)
    return out


def days_info(db, start, end):
    """start..end (dahil) arasındaki her gün için resmi tatil ve özel gün listesi.

    Dönüş: {"YYYY-MM-DD": [{"name", "kind": "resmi"|"ozel", "half": bool}, ...]}
    """
    out = {}
    for year in range(start.year, end.year + 1):
        for d, (name, half) in official_holidays(year).items():
            if start <= d <= end:
                out.setdefault(d.isoformat(), []).append({"name": name, "kind": "resmi", "half": half})
    rows = db.execute(
        "SELECT * FROM special_days WHERE start_date <= ? AND end_date >= ? ORDER BY start_date",
        (end.isoformat(), start.isoformat()),
    ).fetchall()
    for r in rows:
        d = max(date.fromisoformat(r["start_date"]), start)
        last = min(date.fromisoformat(r["end_date"]), end)
        while d <= last:
            out.setdefault(d.isoformat(), []).append({"name": r["name"], "kind": "ozel", "half": False, "id": r["id"]})
            d += timedelta(days=1)
    return out


def upcoming(db, today, days=60):
    """Önümüzdeki günlerdeki tatil/özel günler (ayarlar sayfası ve özet için)."""
    info = days_info(db, today, today + timedelta(days=days))
    return [(date.fromisoformat(k), v) for k, v in sorted(info.items())]
