"""Yedekleme: veritabanının tutarlı kopyası (SQLite backup API) + dekontlar, tek .tar.gz dosyası.

Toplam boyut sınırı aşılınca en eski yedekler silinir; en yeni yedek her zaman korunur.
Eski sürümün sadece veritabanı içeren .db.gz yedekleri de listelenir ve sınır hesabına dahildir.
"""
import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

PREFIX = "odeme-"
SUFFIXES = (".tar.gz", ".db.gz")
NAME_RE = r"odeme-[\w\-]+\.(tar|db)\.gz"


def _files(backup_dir):
    d = Path(backup_dir)
    files = [p for s in SUFFIXES for p in d.glob(f"{PREFIX}*{s}")]
    return sorted(files, key=lambda p: p.name)  # eskiden yeniye (isimde tarih var)


def list_backups(backup_dir):
    return [{"name": p.name, "size": p.stat().st_size,
             "time": datetime.fromtimestamp(p.stat().st_mtime),
             "with_receipts": p.name.endswith(".tar.gz")} for p in reversed(_files(backup_dir))]


def create_backup(db_path, backup_dir, limit_mb=100, label="haftalik", upload_dir=None):
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{PREFIX}{stamp}-{label}.tar.gz"
    upload_dir = Path(upload_dir) if upload_dir else Path(db_path).parent / "uploads"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_db = Path(tmp) / "odeme.db"
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(tmp_db)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        partial = target.with_suffix(".part")
        with tarfile.open(partial, "w:gz", compresslevel=6) as tar:
            tar.add(tmp_db, arcname="odeme.db")
            if upload_dir.is_dir():
                tar.add(upload_dir, arcname="uploads")
        partial.rename(target)

    removed = prune(backup_dir, limit_mb, keep=target.name)
    return target, removed


def prune(backup_dir, limit_mb, keep=None):
    """Toplam boyut sınırı aşılırsa en eski yedekleri siler. En yeni yedek her zaman korunur."""
    limit = float(limit_mb) * 1024 * 1024
    files = _files(backup_dir)
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
