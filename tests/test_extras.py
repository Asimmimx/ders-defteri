"""Kısmi ödeme, veli bakiyesi, işlem geçmişi, raporlar, Telegram, PWA/robots ve yedek testleri."""
import io
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock


class ExtrasTest(unittest.TestCase):
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
        html = self.c.get("/giris").get_data(as_text=True)
        t = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
        self.c.post("/giris", data={"_csrf": t, "username": "asim", "password": "sifre1234"})
        html = self.c.get("/").get_data(as_text=True)
        self.t = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)

    def db(self):
        conn = sqlite3.connect(self.app.config["DATABASE"])
        conn.row_factory = sqlite3.Row
        return conn

    def _parent_student(self, pname, sname, on=1000, inp=1250):
        r = self.c.post("/veliler/yeni", data={"_csrf": self.t, "name": pname, "phone": "05321112233"})
        pid = int(re.search(r"/veliler/(\d+)/", r.headers["Location"]).group(1))
        self.c.post(f"/veliler/{pid}/ogrenci/yeni", data={"_csrf": self.t, "name": sname,
                                                         "online_price": str(on), "inperson_price": str(inp)})
        with self.db() as conn:
            sid = conn.execute("SELECT id FROM students WHERE parent_id = ?", (pid,)).fetchone()[0]
        return pid, sid

    def _new_payment(self, sid, **extra):
        data = {"_csrf": self.t, "student_id": sid, "online_count": "2", "inperson_count": "1", "amount": ""}
        data.update(extra)
        r = self.c.post("/odemeler/yeni", data=data)
        return int(r.headers["Location"].rsplit("/", 1)[1])

    def balance(self, pid):
        from app.main import parent_balance
        with self.app.app_context():
            from app.db import get_db
            return parent_balance(get_db(), pid)

    # ------------------------------------------------------------------ kısmi ödeme
    def test_partial_to_balance_then_carry(self):
        pid, sid = self._parent_student("Kısmi Veli", "Mert")
        p1 = self._new_payment(sid)  # 2*1000 + 1*1250 = 3250
        self.c.post(f"/odemeler/{p1}/tamamla", data={"_csrf": self.t, "paid_amount": "3000", "remainder": "balance"})
        self.assertEqual(self.balance(pid), 250)
        html = self.c.get(f"/veliler/{pid}").get_data(as_text=True)
        self.assertIn("250 ₺", html)
        # Sonraki ödemeye önceki borç eklenir: 3250 + 250 = 3500
        p2 = self._new_payment(sid, include_carry="1")
        with self.db() as conn:
            row = conn.execute("SELECT amount, carry_in FROM payments WHERE id = ?", (p2,)).fetchone()
        self.assertEqual((row["amount"], row["carry_in"]), (3500, 250))
        self.assertEqual(self.balance(pid), 0)
        msg = self.c.get(f"/odemeler/{p2}").get_data(as_text=True)
        self.assertIn("Önceki dönemden kalan: 250 TL", msg)
        self.assertIn("Toplam 3.500 TL", msg)
        # İptal edilirse borç veliye geri döner
        self.c.post(f"/odemeler/{p2}/iptal", data={"_csrf": self.t})
        self.assertEqual(self.balance(pid), 250)

    def test_partial_new_payment_and_overpay(self):
        pid, sid = self._parent_student("Yeni Kalan Veli", "Ece")
        p1 = self._new_payment(sid)
        r = self.c.post(f"/odemeler/{p1}/tamamla", data={"_csrf": self.t, "paid_amount": "3000", "remainder": "new"})
        new_id = int(r.headers["Location"].rsplit("/", 1)[1])
        self.assertNotEqual(new_id, p1)
        with self.db() as conn:
            row = conn.execute("SELECT amount, carry_in, status FROM payments WHERE id = ?", (new_id,)).fetchone()
            first = conn.execute("SELECT commission_amount FROM payments WHERE id = ?", (p1,)).fetchone()
        self.assertEqual((row["amount"], row["carry_in"], row["status"]), (250, 250, "pending"))
        self.assertEqual(first["commission_amount"], 600)  # %20 x 3000
        self.assertEqual(self.balance(pid), 0)
        self.assertIn("Kısmi ödendi", self.c.get("/odemeler?durum=partial").get_data(as_text=True))
        # Fazla ödeme -> alacak
        self.c.post(f"/odemeler/{new_id}/tamamla", data={"_csrf": self.t, "paid_amount": "300"})
        self.assertEqual(self.balance(pid), -50)
        p3 = self._new_payment(sid, include_carry="1")
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT amount FROM payments WHERE id = ?", (p3,)).fetchone()[0], 3200)

    # ------------------------------------------------------------------ işlem geçmişi
    def test_audit_log(self):
        pid, sid = self._parent_student("Log Veli", "Kaan")
        p1 = self._new_payment(sid)
        self.c.post(f"/odemeler/{p1}/tamamla", data={"_csrf": self.t, "paid_amount": "3250"})
        self.c.post(f"/odemeler/{p1}/geri-al", data={"_csrf": self.t})
        with self.db() as conn:
            actions = [r[0] for r in conn.execute(
                "SELECT action FROM audit_log WHERE entity = 'payment' AND entity_id = ? ORDER BY id", (p1,))]
            users = {r[0] for r in conn.execute("SELECT username FROM audit_log WHERE action = 'Veli eklendi'")}
        self.assertEqual(actions, ["Ödeme oluşturuldu", "Ödeme tamamlandı", "Ödeme tekrar bekliyor"])
        self.assertEqual(users, {"Asım"})
        html = self.c.get("/ayarlar/gecmis?q=Log+Veli").get_data(as_text=True)
        self.assertIn("Veli eklendi", html)
        self.assertIn("Bu ödemenin geçmişi", self.c.get(f"/odemeler/{p1}").get_data(as_text=True))
        # Hatalı giriş de kaydedilir
        anon = self.app.test_client()
        html = anon.get("/giris").get_data(as_text=True)
        t = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
        anon.post("/giris", data={"_csrf": t, "username": "asim", "password": "yanlis"})
        with self.db() as conn:
            self.assertTrue(conn.execute("SELECT 1 FROM audit_log WHERE action = 'Hatalı giriş denemesi'").fetchone())

    # ------------------------------------------------------------------ raporlar / döküm
    def test_reports_and_statement(self):
        pid, sid = self._parent_student("Rapor Veli", "Lale")
        p1 = self._new_payment(sid)
        self.c.post(f"/odemeler/{p1}/tamamla", data={"_csrf": self.t, "paid_amount": "3250",
                                                     "paid_at": date.today().isoformat()})
        html = self.c.get("/raporlar").get_data(as_text=True)
        self.assertIn("Lale", html)
        r = self.c.get(f"/raporlar/csv?tur=tahsilat&ay={date.today():%Y-%m}")
        self.assertEqual(r.status_code, 200)
        text = r.get_data().decode("utf-8-sig")
        self.assertIn("Rapor Veli;Lale;2;1;3250;3250", text)
        r = self.c.get("/raporlar/csv?tur=odemeler")
        self.assertIn("Önceki bakiye", r.get_data().decode("utf-8-sig"))
        html = self.c.get(f"/veliler/{pid}/dokum").get_data(as_text=True)
        self.assertIn("Hesap Dökümü", html)
        self.assertIn("3.250 ₺", html)
        self.assertEqual(self.c.get("/daha").status_code, 200)

    # ------------------------------------------------------------------ Telegram
    def test_telegram_summary_and_settings(self):
        from app.notify import daily_summary
        pid, sid = self._parent_student("Telegram Veli", "Arda")
        today = date.today()
        self._new_payment(sid, due_date=today.isoformat())
        self._new_payment(sid, due_date=(today - timedelta(days=3)).isoformat())
        with self.db() as conn:
            text = daily_summary(conn, today)
        self.assertIn("Bugün son gün", text)
        self.assertIn("Geciken", text)
        self.assertIn("Telegram Veli (Arda)", text)
        # Ayar kaydı: hatalı token reddedilir, doğru token kaydedilir ve sayfada gizlenir
        r = self.c.post("/ayarlar/telegram", data={"_csrf": self.t, "telegram_token": "yanlis"}, follow_redirects=True)
        self.assertIn("geçersiz", r.get_data(as_text=True))
        token = "123456789:" + "A" * 35
        self.c.post("/ayarlar/telegram", data={"_csrf": self.t, "telegram_token": token,
                                               "telegram_chat_id": "987654", "telegram_enabled": "1"})
        html = self.c.get("/ayarlar").get_data(as_text=True)
        self.assertNotIn(token, html)
        self.assertIn("…AAAA", html)
        with mock.patch("app.notify.send_message", return_value=(True, None)) as sm:
            r = self.c.post("/ayarlar/telegram/test", data={"_csrf": self.t}, follow_redirects=True)
        self.assertIn("Test mesajı gönderildi", r.get_data(as_text=True))
        self.assertEqual(sm.call_args[0][:2], (token, "987654"))

    # ------------------------------------------------------------------ robots / PWA
    def test_public_files_and_noindex(self):
        anon = self.app.test_client()
        r = anon.get("/robots.txt")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn("User-agent: GPTBot\nDisallow: /", body)
        self.assertIn("User-agent: ClaudeBot\nDisallow: /", body)
        self.assertTrue(body.rstrip().endswith("User-agent: *\nDisallow: /"))
        self.assertEqual(anon.get("/manifest.webmanifest").get_json()["display"], "standalone")
        self.assertIn("serviceWorker", self.c.get("/").get_data(as_text=True))
        self.assertIn("self.addEventListener", anon.get("/sw.js").get_data(as_text=True))
        self.assertIn("noindex", anon.get("/giris").headers["X-Robots-Tag"])
        self.assertIn("noindex", self.c.get("/").headers["X-Robots-Tag"])
        self.assertEqual(anon.get("/").status_code, 302)  # diğer her şey hâlâ girişe yönlenir

    # ------------------------------------------------------------------ yedek: dekontlar dahil
    def test_backup_includes_receipts(self):
        pid, sid = self._parent_student("Yedek Veli", "Nil")
        p1 = self._new_payment(sid)
        self.c.post(f"/odemeler/{p1}/tamamla", data={
            "_csrf": self.t, "paid_amount": "3250", "receipt": (io.BytesIO(b"%PDF-1.4 test"), "dekont.pdf")},
            content_type="multipart/form-data")
        r = self.c.post("/ayarlar/yedek", data={"_csrf": self.t}, follow_redirects=True)
        self.assertIn("Yedek alındı", r.get_data(as_text=True))
        backups = sorted(Path(self.app.config["BACKUP_DIR"]).glob("*.tar.gz"))
        self.assertTrue(backups)
        with tarfile.open(backups[-1]) as tar:
            names = tar.getnames()
        self.assertIn("odeme.db", names)
        self.assertTrue(any(n.startswith("uploads/") and n.endswith(".pdf") for n in names))
        r = self.c.get(f"/ayarlar/yedek/{backups[-1].name}")
        self.assertEqual(r.status_code, 200)
        r.close()


if __name__ == "__main__":
    unittest.main()
