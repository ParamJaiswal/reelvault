"""Backup engine: encrypted, versioned backups of DB + media.

- DB: SQLite online-backup API (safe while the app is running)
- Media: incremental mirror (copy only new/changed files) into a dated folder,
  pruning to keep the last N snapshots
- Encryption (optional): Fernet symmetric encryption of the DB snapshot with a
  key derived from RV_BACKUP_PASSPHRASE; media files are copied as-is
  (they're already user content on disk).
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

from app.core.config import settings

log = logging.getLogger("rv.backup")


def _key_from_passphrase(passphrase: str) -> bytes:
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    import base64

    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=b"reelvault-backup-v1", iterations=240_000)
    return base64.urlsafe_b64encode(
        kdf.derive(passphrase.encode()))


def backup_db(dest_dir: Path, encrypt: bool = False) -> Path:
    """Consistent snapshot of the live SQLite DB."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp = dest_dir / f".db_tmp_{stamp}"
    # online backup through sqlite's backup API
    import sqlite3

    src = sqlite3.connect(str(settings.db_path))
    dst = sqlite3.connect(str(tmp))
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    if encrypt:
        passphrase = settings.backup_passphrase
        if not passphrase:
            log.warning("BACKUP_PASSPHRASE not set — writing plaintext DB")
            out = dest_dir / f"db_{stamp}.sqlite"
            tmp.replace(out)
            return out
        try:
            from cryptography.fernet import Fernet

            f = Fernet(_key_from_passphrase(passphrase))
            token = f.encrypt(tmp.read_bytes())
            out = dest_dir / f"db_{stamp}.sqlite.enc"
            out.write_bytes(token)
            tmp.unlink()
            log.info("db backed up + encrypted -> %s (%d KB)",
                     out.name, len(token) // 1024)
            return out
        except ImportError:
            log.warning("cryptography missing — plaintext fallback")
    out = dest_dir / f"db_{stamp}.sqlite"
    tmp.replace(out)
    return out


def backup_media(dest_root: Path, keep: int = 5) -> tuple[int, int]:
    """Mirror media/ incrementally; prune old snapshots beyond `keep`."""
    src_root: Path = settings.media_dir
    stamp = datetime.now().strftime("%Y-%m-%d")
    snap = dest_root / f"media_{stamp}"
    copied = skipped = 0
    for f in src_root.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(src_root)
        dst = snap / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            h_src = hashlib.md5(f.read_bytes()).hexdigest()
            h_dst = hashlib.md5(dst.read_bytes()).hexdigest()
            if h_src == h_dst:
                skipped += 1
                continue
        shutil.copy2(f, dst)
        copied += 1
    # prune old snapshots (keep newest N distinct folders)
    snaps = sorted(p for p in dest_root.glob("media_*") if p.is_dir())
    for old in snaps[:-keep] if len(snaps) > keep else []:
        shutil.rmtree(old, ignore_errors=True)
    log.info("media mirror -> %s (copied %d, unchanged %d)", snap.name,
             copied, skipped)
    return copied, skipped


def run_backup() -> dict:
    """Full backup run honoring settings; returns a summary."""
    outdir = Path(settings.backup_dir)
    result = {"at": datetime.now().isoformat(timespec="seconds"),
              "db": None, "media_copied": 0, "media_unchanged": 0}
    db_path = backup_db(outdir, encrypt=settings.backup_encrypt)
    result["db"] = str(db_path)
    c, s = backup_media(Path(settings.backup_dir),
                        keep=settings.backup_keep)
    result["media_copied"] = c
    result["media_unchanged"] = s
    return result


def start_scheduler(every_s: int = 86400) -> None:
    """Daily backups in a daemon thread; skips if no directory set."""
    import threading

    def loop():
        time.sleep(300)  # let the app settle after boot
        while True:
            try:
                summary = run_backup()
                log.info("scheduled backup: %s", json.dumps(summary))
            except Exception as e:  # noqa: BLE001
                log.warning("scheduled backup failed: %s", e)
            time.sleep(every_s)

    if settings.backup_dir:
        threading.Thread(target=loop, daemon=True,
                         name="rv-backups").start()
