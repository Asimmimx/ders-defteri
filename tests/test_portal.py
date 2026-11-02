"""Ders kayıtları, veli/öğrenci portalı, portal alan adı ve Google Takvim (taslak) senkronu."""
import io
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def monday(offset_weeks=0):
    d = date.today()
    return (d - timedelta(days=d.weekday()) + timedelta(weeks=offset_weeks)).isoformat()


class PortalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = cls.tmp
        os.environ["SECRET_KEY"] = "test-key"
        os.environ["COOKIE_SECURE"] = "0"
        os.environ.pop("PORTAL_HOST", None)
        from app import create_app
        from werkzeug.security import generate_password_hash
        cls.app = create_app()
        conn = sqlite3.connect(cls.app.config["DATABASE"])
        conn.execute("INSERT INTO users(username, full_name, password_hash) VALUES ('asim', 'Asım', ?)",
                     (generate_password_hash("sifre1234"),))
        conn.execute("INSERT INTO parents(id, name, phone) VALUES (1, 'Nurgül Şahin', '0532 111 22 33')")
        conn.execute("INSERT INTO students(id, parent_id, name, online_price, inperson_price) VALUES (1, 1, 'Ali', 1000, 1250)")
        conn.execute("INSERT INTO students(id, parent_id, name) VALUES (2, 1, 'Ayşe')")
        conn.execute("INSERT INTO parents(id, name) VALUES (2, 'Başka Veli')")
        conn.execute("INSERT INTO students(id, parent_id, name) VALUES (3, 2, 'Can')")
        conn.execute("""INSERT INTO payments(student_id, description, online_count, amount, status, commission_amount)
                        VALUES (1, 'Kasım dersleri', 2, 2000, 'pending', 0)""")
        conn.execute("""INSERT INTO payments(student_id, description, amount, status, notes)
                        VALUES (3, 'Gizli ödeme', 777, 'pending', 'iç not')""")
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------ yardımcılar
    def staff(self):
        c = self.app.test_client()
        html = c.get("/giris").get_data(as_text=True)
        t = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
        c.post("/giris", data={"_csrf": t, "username": "asim", "password": "sifre1234"})
        t = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/").get_data(as_text=True)).group(1)
        return c, t

    def portal_login(self, username, password, client=None, base=""):
        c = client or self.app.test_client()
        html = c.get(f"{base}/portal/giris").get_data(as_text=True)
        t = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
        r = c.post(f"{base}/portal/giris", data={"_csrf": t, "username": username, "password": password})
        return c, r

    def db(self):
        conn = sqlite3.connect(self.app.config["DATABASE"])
        conn.row_factory = sqlite3.Row
        return conn

    def create_account(self, c, t, student_id=None):
        data = {"_csrf": t}
        if student_id:
            data["student_id"] = student_id
        r = c.post("/veliler/1/portal", data=data)
        self.assertEqual(r.status_code, 302)
        html = c.get("/veliler/1").get_data(as_text=True)
        user = re.search(r"Kullanıcı adı: (\S+)", html).group(1)
        pw = re.search(r"Şifre: (\S+)", html).group(1)
        # şifre sadece bir kez gösterilir
        self.assertNotIn(pw, c.get("/veliler/1").get_data(as_text=True))
        return user, pw

    # ------------------------------------------------------------ testler
    def test_lessons_from_schedule_and_portal(self):
        c, t = self.staff()
        h = {"X-CSRF-Token": t}
        week = monday(-1)  # geçen hafta: geçmiş dersler
        sid = c.post("/api/program", json={"week": week, "student_id": 1, "day": 1, "start_min": 600}, headers=h).get_json()["id"]
        sid2 = c.post("/api/program", json={"week": week, "student_id": 3, "day": 2, "start_min": 600}, headers=h).get_json()["id"]
        # Konusu girilmemiş geçmiş dersler listede
        self.assertIn("Konusu girilmemiş", c.get("/dersler").get_data(as_text=True))

        # Programdan konu yaz
        r = c.patch(f"/api/program/{sid}", json={"topic": "Türev: zincir kuralı"}, headers=h)
        self.assertEqual(r.status_code, 200)
        slot = [s for s in c.get(f"/api/program?hafta={week}").get_json()["slots"] if s["id"] == sid][0]
        self.assertEqual(slot["topic"], "Türev: zincir kuralı")
        with self.db() as conn:
            lesson = conn.execute("SELECT * FROM lessons WHERE slot_id = ?", (sid,)).fetchone()
            self.assertEqual(lesson["lesson_date"], (date.fromisoformat(week) + timedelta(days=1)).isoformat())
            self.assertEqual(lesson["start_min"], 600)
            self.assertTrue(conn.execute("SELECT 1 FROM audit_log WHERE action = 'Derste işlenen konu girildi'").fetchone())

        # Ders kaydına PDF + ödev + gizli not; sahte PDF reddedilir
        lid = lesson["id"]
        r = c.post(f"/dersler/{lid}", data={
            "_csrf": t, "lesson_date": lesson["lesson_date"], "topic": "Türev: zincir kuralı", "homework": "s. 45",
            "private_note": "GIZLI-NOT-123",
            "files": [(io.BytesIO(PDF), "turev.pdf"), (io.BytesIO(b"not a pdf"), "sahte.pdf")],
        }, content_type="multipart/form-data", follow_redirects=True)
        body = r.get_data(as_text=True)
        self.assertIn("geçerli bir PDF değil", body)
        self.assertIn("turev.pdf", body)
        with self.db() as conn:
            files = conn.execute("SELECT * FROM lesson_files WHERE lesson_id = ?", (lid,)).fetchall()
        self.assertEqual(len(files), 1)
        fid = files[0]["id"]
        self.assertEqual(c.get(f"/dersler/dosya/{fid}").status_code, 200)

        # Diğer ailenin dersine dosya (veli 1 göremeyecek)
        c.patch(f"/api/program/{sid2}", json={"topic": "Başka aile konusu"}, headers=h)
        with self.db() as conn:
            lid2 = conn.execute("SELECT id FROM lessons WHERE slot_id = ?", (sid2,)).fetchone()[0]
        c.post(f"/dersler/{lid2}", data={"_csrf": t, "lesson_date": "2026-01-01", "topic": "Başka aile konusu",
                                          "files": [(io.BytesIO(PDF), "baska.pdf")]}, content_type="multipart/form-data")
        with self.db() as conn:
            fid2 = conn.execute("SELECT id FROM lesson_files WHERE lesson_id = ?", (lid2,)).fetchone()[0]

        # Ders taşınınca kayıt da taşınır
        c.patch(f"/api/program/{sid}", json={"day": 2, "start_min": 660}, headers=h)
        with self.db() as conn:
            moved = conn.execute("SELECT * FROM lessons WHERE id = ?", (lid,)).fetchone()
        self.assertEqual(moved["start_min"], 660)
        self.assertEqual(moved["lesson_date"], (date.fromisoformat(week) + timedelta(days=2)).isoformat())

        # Ders silinince dolu kayıt korunur, geri alınınca yeniden bağlanır
        c.delete(f"/api/program/{sid}", headers=h)
        with self.db() as conn:
            self.assertIsNone(conn.execute("SELECT slot_id FROM lessons WHERE id = ?", (lid,)).fetchone()[0])
        new_sid = c.post("/api/program", json={"week": week, "student_id": 1, "day": 2, "start_min": 660},
                         headers=h).get_json()["id"]
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT slot_id FROM lessons WHERE id = ?", (lid,)).fetchone()[0], new_sid)

        # Boş kayıt, ders silinince temizlenir
        empty = c.post("/api/program", json={"week": week, "student_id": 2, "day": 4, "start_min": 600}, headers=h).get_json()["id"]
        c.get(f"/dersler/program/{empty}")
        c.delete(f"/api/program/{empty}", headers=h)
        with self.db() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM lessons WHERE slot_id IS NULL AND student_id = 2").fetchone())

        # Bu hafta için gelecek ders (programda görünmeli)
        c.post("/api/program", json={"week": monday(1), "student_id": 2, "day": 0, "start_min": 900}, headers=h)

        # Elle ders kaydı (programda olmayan)
        r = c.post("/dersler/yeni", data={"_csrf": t, "student_id": 2, "lesson_date": week, "start_time": "15:30",
                                          "lesson_type": "online", "topic": "Elle girilen konu"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("Elle girilen konu", c.get("/dersler?ogrenci=2").get_data(as_text=True))

        # ---------------- Veli hesabı
        user, pw = self.create_account(c, t)
        self.assertEqual(user, "nurgul.sahin")
        pc, r = self.portal_login(user, "yanlis")
        self.assertIn("hatalı", r.get_data(as_text=True))
        pc, r = self.portal_login(user, pw)
        self.assertEqual(r.status_code, 302)
        home = pc.get("/portal/").get_data(as_text=True)
        self.assertIn("Merhaba Nurgül Şahin", home)
        self.assertIn("Türev: zincir kuralı", home)
        self.assertIn("2.000 ₺", home)  # bekleyen ödeme
        lessons = pc.get("/portal/dersler").get_data(as_text=True)
        self.assertIn("Elle girilen konu", lessons)
        self.assertIn("s. 45", lessons)
        self.assertNotIn("GIZLI-NOT-123", lessons)
        self.assertNotIn("Başka aile konusu", lessons)
        self.assertEqual(pc.get(f"/portal/dosya/{fid}").status_code, 200)
        self.assertEqual(pc.get(f"/portal/dosya/{fid2}").status_code, 404)
        pay = pc.get("/portal/odemeler").get_data(as_text=True)
        self.assertIn("Kasım dersleri", pay)
        self.assertNotIn("Gizli ödeme", pay)
        self.assertNotIn("omisyon", pay)
        prog = pc.get(f"/portal/program?hafta={monday(1)}").get_data(as_text=True)
        self.assertIn("Ayşe", prog)
        self.assertIn("15:00", prog)
        # Portal oturumu personel sayfalarını açmaz
        self.assertIn("/giris", pc.get("/veliler").headers["Location"])
        self.assertEqual(pc.get("/api/program").status_code, 401)
        self.assertEqual(pc.get(f"/dersler/dosya/{fid}").status_code, 302)

        # Şifre değiştir
        t2 = re.search(r'name="csrf-token" content="([^"]+)"', home).group(1)
        r = pc.post("/portal/sifre", data={"_csrf": t2, "current": pw, "new": "yenisifre99", "new2": "yenisifre99"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.portal_login(user, "yenisifre99")[1].status_code, 302)

        # ---------------- Öğrenci hesabı: ödeme göremez
        suser, spw = self.create_account(c, t, student_id=1)
        self.assertEqual(suser, "ali")
        sc, r = self.portal_login(suser, spw)
        self.assertEqual(r.status_code, 302)
        shome = sc.get("/portal/").get_data(as_text=True)
        self.assertIn("Türev: zincir kuralı", shome)
        self.assertNotIn("Bekleyen ödeme", shome)
        self.assertNotIn("Ödemeler", shome)
        self.assertEqual(sc.get("/portal/odemeler").status_code, 404)
        self.assertNotIn("Elle girilen konu", sc.get("/portal/dersler").get_data(as_text=True))  # Ayşe'nin
        self.assertEqual(sc.get(f"/portal/dosya/{fid}").status_code, 200)

        # Hesap kapatılınca oturum düşer
        with self.db() as conn:
            uid = conn.execute("SELECT id FROM portal_users WHERE username = ?", (suser,)).fetchone()[0]
        c.post(f"/portal-hesap/{uid}/durum", data={"_csrf": t})
        self.assertIn("/portal/giris", sc.get("/portal/").headers["Location"])
        # Şifre sıfırlama yeni şifre üretir
        c.post(f"/portal-hesap/{uid}/durum", data={"_csrf": t})
        c.post(f"/portal-hesap/{uid}/sifirla", data={"_csrf": t})
        html = c.get("/veliler/1").get_data(as_text=True)
        new_pw = re.search(r"Şifre: (\S+)", html).group(1)
        self.assertNotEqual(new_pw, spw)
        self.assertEqual(self.portal_login(suser, spw)[1].status_code, 200)  # eski şifre çalışmaz
        self.assertEqual(self.portal_login(suser, new_pw)[1].status_code, 302)
        with self.db() as conn:
            actions = {r[0] for r in conn.execute("SELECT action FROM audit_log")}
        for a in ("Portal hesabı oluşturuldu", "Portal şifresi sıfırlandı", "Portala giriş yapıldı",
                  "Ders kaydı güncellendi", "Ders kaydı eklendi", "Portal hesabı kapatıldı"):
            self.assertIn(a, actions)

    def test_portal_host_only_serves_portal(self):
        self.app.config["PORTAL_HOST"] = "portal.example.test"
        try:
            c = self.app.test_client()
            base = "http://portal.example.test"
            r = c.get(f"{base}/")
            self.assertEqual(r.status_code, 302)
            self.assertIn("/portal/", r.headers["Location"])
            self.assertEqual(c.get(f"{base}/veliler").status_code, 404)
            self.assertEqual(c.get(f"{base}/api/program").status_code, 404)
            self.assertEqual(c.get(f"{base}/portal/giris").status_code, 200)
            self.assertEqual(c.get(f"{base}/robots.txt").status_code, 200)
            self.assertIn("noindex", c.get(f"{base}/portal/giris").headers["X-Robots-Tag"])
            # Personel girişi portal alan adında açılmaz
            self.assertIn("/portal/", c.get(f"{base}/giris").headers["Location"])
            # Personel alan adı etkilenmez
            self.assertIn("/giris", self.app.test_client().get("http://odeme.example.test/").headers["Location"])
        finally:
            self.app.config["PORTAL_HOST"] = ""

    def test_gcal_sync_with_fake_google(self):
        from app import gcal
        c, t = self.staff()
        h = {"X-CSRF-Token": t}
        # Bağlı değilken senkron hiçbir şey yapmaz
        with self.app.app_context():
            from app.db import get_db
            self.assertIsNone(gcal.sync(get_db()))
        week = monday(0)
        sid = c.post("/api/program", json={"week": week, "student_id": 2, "day": 3, "start_min": 1020}, headers=h).get_json()["id"]
        with self.db() as conn:
            for k, v in (("gcal_client_id", "x.apps.googleusercontent.com"), ("gcal_client_secret", "s"),
                         ("gcal_refresh_token", "r")):
                conn.execute("UPDATE settings SET value = ? WHERE key = ?", (v, k))
            conn.execute("INSERT INTO gcal_trash(event_id) VALUES ('eski-etkinlik')")
        d = date.fromisoformat(week) + timedelta(days=4)  # cuma
        calls = []

        def fake_http(method, url, data=None, token=None, form=False):
            calls.append((method, url.split("?")[0], data))
            if url.startswith(gcal.TOKEN_URL):
                return 200, {"access_token": "a", "expires_in": 3600}
            if method == "DELETE":
                return 410, {}
            if method == "POST":
                return 200, {"id": f"ev{len(calls)}"}
            if method == "GET":
                return 200, {"items": [
                    {"id": "g1", "summary": "Okul toplantısı", "status": "confirmed",
                     "start": {"dateTime": f"{d.isoformat()}T13:30:00+03:00"},
                     "end": {"dateTime": f"{d.isoformat()}T15:00:00+03:00"}},
                    {"id": "g2", "summary": "Doğum günü", "start": {"date": d.isoformat()}, "end": {"date": d.isoformat()}},
                    {"id": "g3", "summary": "Bizim ders", "start": {"dateTime": f"{d.isoformat()}T10:00:00+03:00"},
                     "end": {"dateTime": f"{d.isoformat()}T11:00:00+03:00"},
                     "extendedProperties": {"private": {gcal.PROP: "5"}}},
                ]}
            return 200, {}

        orig = gcal._http
        gcal._http = fake_http
        gcal._token_cache.clear()
        try:
            r = c.post("/google/senkron", data={"_csrf": t}, follow_redirects=True)
            self.assertIn("ders gönderildi", r.get_data(as_text=True))
        finally:
            gcal._http = orig
        posts = [x for x in calls if x[0] == "POST" and "events" in x[1]]
        self.assertTrue(any(p[2]["summary"].startswith("Ders: Ayşe") for p in posts))
        ayse = [p for p in posts if p[2]["summary"].startswith("Ders: Ayşe")][0][2]
        self.assertTrue(ayse["start"]["dateTime"].endswith("T17:00:00"))
        with self.db() as conn:
            slot = conn.execute("SELECT * FROM schedule_slots WHERE id = ?", (sid,)).fetchone()
            self.assertEqual(slot["gcal_synced"], 1)
            self.assertTrue(slot["gcal_event_id"].startswith("ev"))
            self.assertIsNone(conn.execute("SELECT 1 FROM gcal_trash").fetchone())
            busy = conn.execute("SELECT * FROM busy_blocks WHERE source = 'gcal' ORDER BY start_min").fetchall()
        # 13:30–15:00 → 13:00 ve 14:00 hücreleri dolu; tüm gün ve bizim etkinlik atlanır
        self.assertEqual([(b["day"], b["start_min"]) for b in busy], [(4, 780), (4, 840)])
        self.assertEqual(busy[0]["label"], "Google: Okul toplantısı")
        # Ders güncellenince yeniden gönderilmek üzere işaretlenir; silinince Google'dan silinecekler listesine girer
        c.patch(f"/api/program/{sid}", json={"start_min": 1080}, headers=h)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT gcal_synced FROM schedule_slots WHERE id = ?", (sid,)).fetchone()[0], 0)
        c.delete(f"/api/program/{sid}", headers=h)
        with self.db() as conn:
            self.assertTrue(conn.execute("SELECT 1 FROM gcal_trash WHERE event_id = ?", (slot["gcal_event_id"],)).fetchone())
        # Bağlantıyı kes: Google'dan gelen dolu saatler kalkar
        c.post("/google/kes", data={"_csrf": t})
        with self.db() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM busy_blocks WHERE source = 'gcal'").fetchone())
            self.assertEqual(conn.execute("SELECT value FROM settings WHERE key = 'gcal_refresh_token'").fetchone()[0], "")
        self.assertIn("Taslak", c.get("/ayarlar").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
