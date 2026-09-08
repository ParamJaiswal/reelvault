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
            # Failing loudly beats a silent plaintext copy of the DB (which
            # contains session/auth data) sitting on disk unnoticed.
            tmp.unlink(missing_ok=True)
            raise RuntimeError(
                "BACKUP_PASSPHRASE not set (RV_BACKUP_PASSPHRASE) — "
                "refusing to write a plaintext DB snapshot; "
                "set a passphrase or set RV_BACKUP_ENCRYPT=0")
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


def restore_db(src: Path, dest_dir: Path, passphrase: str | None = None) -> Path:
    """Decrypt (if needed) + verify a DB snapshot into dest_dir.

    AGENTS.md §12: a backup is not "valid" until restored into a clean
    location and the app can read it. Returns the restored DB path.
    Raises on a missing/mismatched passphrase or a failed integrity check.
    """
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(f"snapshot not found: {src}")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / "reelvault.db"
    raw = src.read_bytes()
    if src.suffix == ".enc":
        if not passphrase:
            passphrase = settings.backup_passphrase
        if not passphrase:
            raise RuntimeError(
                "snapshot is encrypted — provide the passphrase")
        from cryptography.fernet import Fernet

        f = Fernet(_key_from_passphrase(passphrase))
        raw = f.decrypt(raw)  # raises InvalidToken on a wrong passphrase
    tmp = dest_dir / ".restore_tmp"
    tmp.write_bytes(raw)
    import sqlite3

    check = sqlite3.connect(str(tmp))
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
        n_reels = check.execute("SELECT COUNT(*) FROM reels").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"restored DB failed integrity check: {result}")
    tmp.replace(out)
    log.info("restored %s -> %s (%d reels, integrity ok)",
             src.name, out, n_reels)
    return out


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
