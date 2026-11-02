"""Yönetim komutları.

    python manage.py create-user <kullanici_adi> [--name "Ad Soyad"]
    python manage.py set-password <kullanici_adi>
    python manage.py backup            # haftalık yedek (systemd timer bunu çalıştırır)
    python manage.py run               # geliştirme sunucusu (http://127.0.0.1:5000)
"""
import argparse
import getpass
import sys

from werkzeug.security import generate_password_hash

from app import create_app
from app.backup import create_backup
from app.db import connect, DEFAULT_SETTINGS


def _ask_password():
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    while True:
        p1 = getpass.getpass("Şifre: ")
        if len(p1) < 8:
            print("En az 8 karakter olmalı.")
            continue
        if p1 == getpass.getpass("Şifre (tekrar): "):
            return p1
        print("Şifreler eşleşmiyor.")


def main():
    parser = argparse.ArgumentParser(description="Ödeme takip yönetim komutları")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user")
    cu.add_argument("username")
    cu.add_argument("--name", default="")
    sp = sub.add_parser("set-password")
    sp.add_argument("username")
    sub.add_parser("backup")
    rn = sub.add_parser("run")
    rn.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    app = create_app()
    db_path = app.config["DATABASE"]

    if args.cmd == "run":
        app.run(host="127.0.0.1", port=args.port, debug=True)
        return

    conn = connect(db_path)
    if args.cmd == "create-user":
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (args.username,)).fetchone():
            sys.exit("Bu kullanıcı zaten var. Şifre değiştirmek için: set-password")
        pw = _ask_password()
        conn.execute("INSERT INTO users(username, full_name, password_hash) VALUES (?, ?, ?)",
                     (args.username, args.name, generate_password_hash(pw)))
        conn.commit()
        print(f"Kullanıcı oluşturuldu: {args.username}")
    elif args.cmd == "set-password":
        pw = _ask_password()
        cur = conn.execute("UPDATE users SET password_hash = ? WHERE username = ?",
                           (generate_password_hash(pw), args.username))
        conn.commit()
        print("Şifre güncellendi." if cur.rowcount else "Kullanıcı bulunamadı.")
    elif args.cmd == "backup":
        row = conn.execute("SELECT value FROM settings WHERE key = 'backup_limit_mb'").fetchone()
        limit = float(row[0]) if row else float(DEFAULT_SETTINGS["backup_limit_mb"])
        target, removed = create_backup(db_path, app.config["BACKUP_DIR"], limit)
        print(f"Yedek: {target}")
        for name in removed:
            print(f"Silindi (sınır {limit:g} MB): {name}")
    conn.close()


if __name__ == "__main__":
    main()
