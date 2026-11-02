"""Uçtan uca duman testi:  python -m unittest discover tests"""
import io
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path


class AppFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = cls.tmp
        os.environ["SECRET_KEY"] = "test-key"
        os.environ["COOKIE_SECURE"] = "0"
        from app import create_app
        from werkzeug.security import generate_password_hash
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        conn = sqlite3.connect(cls.app.config["DATABASE"])
        conn.execute("INSERT INTO users(username, full_name, password_hash) VALUES (?, ?, ?)",
                     ("asim", "Asım", generate_password_hash("sifre1234")))
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.c = self.app.test_client()

    def csrf(self, path="/giris"):
        html = self.c.get(path).get_data(as_text=True)
        m = re.search(r'name="_csrf" value="([^"]+)"', html) or re.search(r'name="csrf-token" content="([^"]+)"', html)
        return m.group(1)

    def login(self):
        token = self.csrf()
        r = self.c.post("/giris", data={"_csrf": token, "username": "asim", "password": "sifre1234"})
        self.assertEqual(r.status_code, 302)
        return self.csrf("/")

    def db(self):
        conn = sqlite3.connect(self.app.config["DATABASE"])
        conn.row_factory = sqlite3.Row
        return conn

    def test_requires_login(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/giris", r.headers["Location"])
        self.assertEqual(self.c.get("/api/program").status_code, 401)

    def test_wrong_password_and_csrf(self):
        token = self.csrf()
        r = self.c.post("/giris", data={"_csrf": token, "username": "asim", "password": "yanlis"})
        self.assertIn("hatalı", r.get_data(as_text=True))
        r = self.c.post("/giris", data={"username": "asim", "password": "sifre1234"})
        self.assertEqual(r.status_code, 400)

    def test_full_flow(self):
        t = self.login()
        # Veli + öğrenci
        r = self.c.post("/veliler/yeni", data={"_csrf": t, "name": "Nurgül Hanım", "phone": "0532 111 22 33"})
        self.assertEqual(r.status_code, 302)
        pid = int(re.search(r"/veliler/(\d+)/", r.headers["Location"]).group(1))
        r = self.c.post(f"/veliler/{pid}/ogrenci/yeni", data={
            "_csrf": t, "name": "Ali", "color": "#27ae60", "online_price": "1000", "inperson_price": "1.250",
            "commission_type": "", "commission_value": ""})
        self.assertEqual(r.status_code, 302)
        with self.db() as conn:
            st = conn.execute("SELECT * FROM students WHERE name = 'Ali'").fetchone()
        self.assertEqual(st["inperson_price"], 1250)

        # Ödeme: 1 online + 1 yüz yüze = 2250 (boş tutar -> otomatik)
        r = self.c.post("/odemeler/yeni", data={"_csrf": t, "student_id": st["id"], "online_count": "1",
                                                 "inperson_count": "1", "amount": "", "due_date": "2026-11-10"})
        self.assertEqual(r.status_code, 302)
        pay_id = int(r.headers["Location"].rsplit("/", 1)[1])
        html = self.c.get(f"/odemeler/{pay_id}").get_data(as_text=True)
        self.assertIn("2.250 ₺", html)
        self.assertIn("Nurgül Hanım", html)       # SMS metninde veli adı
        self.assertIn("sms:+905321112233", html)  # SMS linki

        # Hatırlatıldı işaretle (fetch)
        r = self.c.post(f"/odemeler/{pay_id}/hatirlatildi", headers={"X-CSRF-Token": t, "X-Requested-With": "fetch"})
        self.assertEqual(r.get_json(), {"ok": True})

        # Ödemeyi dekontla tamamla; varsayılan %20 -> 450
        r = self.c.post(f"/odemeler/{pay_id}/tamamla", data={
            "_csrf": t, "paid_amount": "2250", "paid_at": "2026-11-02", "method": "Havale/EFT",
            "receipt": (io.BytesIO(b"\x89PNG\r\n\x1a\nfake"), "dekont.png")},
            content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        with self.db() as conn:
            p = conn.execute("SELECT * FROM payments WHERE id = ?", (pay_id,)).fetchone()
        self.assertEqual(p["status"], "paid")
        self.assertEqual(p["commission_amount"], 450)
        self.assertTrue((Path(self.app.config["UPLOAD_DIR"]) / p["receipt_path"]).exists())
        self.assertEqual(self.c.get(f"/odemeler/{pay_id}/dekont").status_code, 200)

        # Kötü uzantı reddedilir
        r = self.c.post(f"/odemeler/{pay_id}/dekont", data={
            "_csrf": t, "receipt": (io.BytesIO(b"x"), "zararli.exe")}, content_type="multipart/form-data",
            follow_redirects=True)
        self.assertIn("sadece resim", r.get_data(as_text=True))

        # Komisyon: 450 kazanıldı, 200 ödendi -> 250 bakiye
        self.c.post("/komisyon/odeme", data={"_csrf": t, "amount": "200", "paid_on": "2026-11-02"})
        html = self.c.get("/komisyon").get_data(as_text=True)
        self.assertIn("250 ₺", html)

        # Sabit komisyonlu öğrenci: ders başı 100 -> 3 ders = 300
        self.c.post(f"/veliler/{pid}/ogrenci/yeni", data={
            "_csrf": t, "name": "Ayşe", "online_price": "1200", "inperson_price": "1500",
            "commission_type": "fixed", "commission_value": "100"})
        with self.db() as conn:
            ay = conn.execute("SELECT id FROM students WHERE name = 'Ayşe'").fetchone()["id"]
        r = self.c.post("/odemeler/yeni", data={"_csrf": t, "student_id": ay, "online_count": "3", "inperson_count": "0"})
        pay2 = int(r.headers["Location"].rsplit("/", 1)[1])
        self.c.post(f"/odemeler/{pay2}/tamamla", data={"_csrf": t, "paid_amount": "3600"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT commission_amount FROM payments WHERE id = ?", (pay2,)).fetchone()[0], 300)

        # Geri al -> komisyon sıfırlanır
        self.c.post(f"/odemeler/{pay2}/geri-al", data={"_csrf": t})
        with self.db() as conn:
            row = conn.execute("SELECT status, commission_amount FROM payments WHERE id = ?", (pay2,)).fetchone()
        self.assertEqual((row[0], row[1]), ("pending", 0))

        # Ödemesi olan veli silinemez
        r = self.c.post(f"/veliler/{pid}/sil", data={"_csrf": t}, follow_redirects=True)
        self.assertIn("Pasif", r.get_data(as_text=True))

        # Küsuratlı saat: 1,5 saat online + 1 saat yüz yüze = 1500 + 1250 = 2750
        r = self.c.post("/odemeler/yeni", data={"_csrf": t, "student_id": st["id"], "online_count": "1,5",
                                                 "inperson_count": "1", "amount": ""})
        pay3 = int(r.headers["Location"].rsplit("/", 1)[1])
        with self.db() as conn:
            row = conn.execute("SELECT * FROM payments WHERE id = ?", (pay3,)).fetchone()
        self.assertEqual((row["online_count"], row["amount"], row["online_unit"]), (1.5, 2750, 1000))
        html = self.c.get(f"/odemeler/{pay3}").get_data(as_text=True)
        self.assertIn("1,5 saat online(1,5x1000)", html)
        self.assertIn("1 saat yüz yüze(1x1250)", html)
        self.assertIn("Toplam 2.750 TL ödeme göndermeniz uygundur", html)
        # Tarife değişse de eski ödemenin birim fiyatı korunur
        self.c.post(f"/ogrenciler/{st['id']}/duzenle", data={"_csrf": t, "name": "Ali", "online_price": "2000",
                                                              "inperson_price": "1250", "active": "1"})
        self.assertIn("1,5x1000", self.c.get(f"/odemeler/{pay3}").get_data(as_text=True))

        # Ödemeler varsayılan "Tümü" + özet kartları
        html = self.c.get("/odemeler").get_data(as_text=True)
        self.assertIn("Komisyon bakiyem", html)
        self.assertIn('class="active">Tümü', html)

        # Notlar
        self.c.post("/notlar", data={"_csrf": t, "body": "Cuma dersi 16:00'ya alındı", "pinned": "1"})
        html = self.c.get("/notlar").get_data(as_text=True)
        self.assertIn("Cuma dersi 16:00", html)
        with self.db() as conn:
            nid = conn.execute("SELECT id FROM notes").fetchone()[0]
        self.c.post(f"/notlar/{nid}/tamamla", data={"_csrf": t})
        self.assertNotIn("Cuma dersi", self.c.get("/notlar").get_data(as_text=True))
        self.assertIn("Cuma dersi", self.c.get("/notlar?goster=tamam").get_data(as_text=True))

        # Program API
        h = {"X-CSRF-Token": t}
        r = self.c.post("/api/program", json={"student_id": st["id"], "day": 0, "start_min": 600,
                                              "lesson_type": "online"}, headers=h)
        self.assertEqual(r.status_code, 201)
        sid = r.get_json()["id"]
        self.assertEqual(self.c.patch(f"/api/program/{sid}", json={"day": 2, "start_min": 660}, headers=h).status_code, 200)
        self.assertEqual(self.c.post("/api/program", json={"student_id": st["id"], "day": 0, "start_min": 60}, headers=h).status_code, 400)
        slots = self.c.get("/api/program").get_json()["slots"]
        self.assertEqual((slots[0]["day"], slots[0]["start_min"], slots[0]["lesson_type"]), (2, 660, "online"))
        self.assertEqual(self.c.delete(f"/api/program/{sid}", headers=h).status_code, 200)

        # Tüm sayfalar açılıyor
        for path in ["/", "/veliler", f"/veliler/{pid}", "/odemeler", "/odemeler?durum=paid", "/odemeler/yeni",
                     f"/odemeler/{pay_id}/duzenle", "/program", "/komisyon", "/ayarlar", "/notlar",
                     f"/ogrenciler/{st['id']}/duzenle", f"/veliler/{pid}/duzenle"]:
            self.assertEqual(self.c.get(path).status_code, 200, path)

        # Elle yedek
        r = self.c.post("/ayarlar/yedek", data={"_csrf": t}, follow_redirects=True)
        self.assertIn("Yedek alındı", r.get_data(as_text=True))


class BackupPruneTest(unittest.TestCase):
    def test_prune_keeps_limit_and_newest(self):
        from app.backup import prune
        d = Path(tempfile.mkdtemp())
        try:
            for i in range(5):
                (d / f"odeme-2026010{i}-000000-haftalik.db.gz").write_bytes(b"x" * 400_000)
            removed = prune(d, 1)  # 1 MB sınır, 2 MB veri
            left = sorted(p.name for p in d.iterdir())
            self.assertEqual(len(removed), 3)
            self.assertEqual(left, ["odeme-20260103-000000-haftalik.db.gz", "odeme-20260104-000000-haftalik.db.gz"])
            # Tek yedek sınırdan büyükse bile silinmez
            prune(d, 0.0001)
            self.assertEqual(len(list(d.iterdir())), 1)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
