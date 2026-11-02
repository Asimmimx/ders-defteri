"""Haftalık (tarihli) program, resmi tatiller ve özel günler."""
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date


class WeeksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = cls.tmp
        os.environ["SECRET_KEY"] = "test-key"
        os.environ["COOKIE_SECURE"] = "0"
        from app import create_app
        from werkzeug.security import generate_password_hash
        cls.app = create_app()
        conn = sqlite3.connect(cls.app.config["DATABASE"])
        conn.execute("INSERT INTO users(username, full_name, password_hash) VALUES ('asim', 'Asım', ?)",
                     (generate_password_hash("sifre1234"),))
        conn.execute("INSERT INTO parents(name) VALUES ('Veli')")
        conn.execute("INSERT INTO students(parent_id, name) VALUES (1, 'Ali')")
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.c = self.app.test_client()
        html = self.c.get("/giris").get_data(as_text=True)
        t = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
        self.c.post("/giris", data={"_csrf": t, "username": "asim", "password": "sifre1234"})
        html = self.c.get("/").get_data(as_text=True)
        self.t = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
        self.h = {"X-CSRF-Token": self.t}

    def add(self, week, day, start, sid=1):
        return self.c.post("/api/program", json={"week": week, "student_id": sid, "day": day, "start_min": start},
                           headers=self.h)

    def get(self, week):
        return self.c.get(f"/api/program?hafta={week}").get_json()

    def test_holidays(self):
        from app.holidays import official_holidays
        h = official_holidays(2026)
        self.assertEqual(h[date(2026, 10, 29)][0], "Cumhuriyet Bayramı")
        self.assertTrue(h[date(2026, 10, 28)][1])  # arife yarım gün
        self.assertIn("Ramazan Bayramı", h[date(2026, 3, 20)][0])
        self.assertIn("Kurban Bayramı", h[date(2026, 5, 30)][0])  # 4. gün
        self.assertTrue(h[date(2026, 5, 26)][1])  # Kurban arifesi
        # 26 Ekim 2026 haftası: 28 Ekim yarım gün, 29 Ekim tatil
        data = self.get("2026-10-26")
        self.assertEqual(data["dates"][0]["iso"], "2026-10-26")
        self.assertEqual(data["dates"][3]["day"], 29)
        self.assertEqual(data["days"]["2026-10-29"][0]["kind"], "resmi")
        self.assertTrue(data["days"]["2026-10-28"][0]["half"])

    def test_weeks_are_separate_copy_and_clear(self):
        w1, w2 = "2026-11-02", "2026-11-09"
        self.assertEqual(self.add(w1, 0, 600).status_code, 201)
        self.c.post("/api/dolu", json={"week": w1, "day": 2, "start_min": 780, "count": 2, "label": "Okul"},
                    headers=self.h)
        self.assertEqual(len(self.get(w1)["slots"]), 1)
        self.assertEqual(self.get(w2)["slots"], [])
        self.assertEqual(self.get(w2)["prev_count"], 3)
        # Hafta pazartesi değilse reddedilir
        self.assertEqual(self.add("2026-11-03", 0, 600).status_code, 400)
        # Geçen haftayı kopyala
        r = self.c.post("/api/program/kopyala", json={"week": w2}, headers=self.h).get_json()
        self.assertEqual((r["added"], r["skipped"]), (3, 0))
        d2 = self.get(w2)
        self.assertEqual(len(d2["slots"]), 1)
        self.assertEqual(sorted(b["start_min"] for b in d2["busy"]), [780, 840])
        # Tekrar kopyalamak çift kayıt yapmaz
        r = self.c.post("/api/program/kopyala", json={"week": w2}, headers=self.h).get_json()
        self.assertEqual(r["added"], 0)
        # Dolu saat sadece kendi haftasını etkiler: w1'deki Çarşamba 13:00 dolu, w3'te değil
        self.assertEqual(self.add(w1, 2, 780).status_code, 409)
        self.assertEqual(self.add("2026-11-16", 2, 780).status_code, 201)
        # Temizle
        r = self.c.post("/api/program/temizle", json={"week": w2}, headers=self.h).get_json()
        self.assertEqual((r["lessons"], r["busy"]), (1, 2))
        self.assertEqual(self.get(w2)["slots"], [])
        self.assertEqual(len(self.get(w1)["slots"]), 1)

    def test_special_days_and_page(self):
        r = self.c.post("/ayarlar/ozel-gun", data={"_csrf": self.t, "name": "Yarıyıl tatili",
                                                   "start_date": "2027-01-25", "end_date": "2027-01-27"})
        self.assertEqual(r.status_code, 302)
        days = self.get("2027-01-25")["days"]
        self.assertEqual(days["2027-01-26"][0], {"name": "Yarıyıl tatili", "kind": "ozel", "half": False,
                                                 "id": days["2027-01-26"][0]["id"]})
        self.assertNotIn("2027-01-28", days)
        html = self.c.get("/program?hafta=2026-10-28").get_data(as_text=True)  # haftanın ortası -> pazartesiye
        self.assertIn('data-week="2026-10-26"', html)
        self.assertIn("26 Ekim – 1 Kasım 2026", html)
        self.assertIn("Yarıyıl tatili", self.c.get("/ayarlar").get_data(as_text=True))

    def test_migration_assigns_current_week(self):
        from app.db import init_db
        from app.schedule import monday_of
        path = os.path.join(self.tmp, "eski.db")
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE schedule_slots (id INTEGER PRIMARY KEY, student_id INTEGER, day INTEGER,
                start_min INTEGER, lesson_type TEXT DEFAULT 'inperson', note TEXT DEFAULT '');
            CREATE TABLE busy_blocks (id INTEGER PRIMARY KEY, day INTEGER, start_min INTEGER, label TEXT DEFAULT '');
            INSERT INTO schedule_slots(student_id, day, start_min) VALUES (1, 0, 600);
            INSERT INTO busy_blocks(day, start_min) VALUES (1, 600);
        """)
        conn.commit()
        conn.close()
        init_db(path)
        conn = sqlite3.connect(path)
        w = monday_of().isoformat()
        self.assertEqual(conn.execute("SELECT week_start FROM schedule_slots").fetchone()[0], w)
        self.assertEqual(conn.execute("SELECT week_start FROM busy_blocks").fetchone()[0], w)
        conn.close()


if __name__ == "__main__":
    unittest.main()
