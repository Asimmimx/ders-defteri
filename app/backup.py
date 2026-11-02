"""Veritabanı yedekleme: tutarlı kopya (SQLite backup API) + gzip + boyut sınırı."""
import gzip
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

PREFIX = "odeme-"
SUFFIX = ".db.gz"


def list_backups(backup_dir):
    files = sorted(Path(backup_dir).glob(f"{PREFIX}*{SUFFIX}"), key=lambda p: p.name)
    return [{"name": p.name, "size": p.stat().st_size,
             "time": datetime.fromtimestamp(p.stat().st_mtime)} for p in reversed(files)]


def create_backup(db_path, backup_dir, limit_mb=100, label="haftalik"):
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{PREFIX}{stamp}-{label}{SUFFIX}"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_db = Path(tmp) / "copy.db"
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(tmp_db)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        with open(tmp_db, "rb") as fin, gzip.open(target, "wb", compresslevel=9) as fout:
            shutil.copyfileobj(fin, fout)

    removed = prune(backup_dir, limit_mb, keep=target.name)
    return target, removed


def prune(backup_dir, limit_mb, keep=None):
    """Toplam boyut sınırı aşılırsa en eski yedekleri siler. En yeni yedek her zaman korunur."""
    limit = float(limit_mb) * 1024 * 1024
    files = sorted(Path(backup_dir).glob(f"{PREFIX}*{SUFFIX}"), key=lambda p: p.name)  # eskiden yeniye
    total = sum(p.stat().st_size for p in files)
    if keep is None and files:
        keep = files[-1].name
    candidates = [p for p in files if p.name != keep]
    removed = []
    while total > limit and candidates:
        oldest = candidates.pop(0)
        total -= oldest.stat().st_size
        oldest.unlink()
        removed.append(oldest.name)
    return removed
