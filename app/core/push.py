"""Web Push (VAPID) notifications + deadline scanner.

- VAPID keypair auto-generated into data/push_vapid.json on first use.
- Subscriptions stored per user; dead endpoints pruned on 404/410.
- notify_user() fans out to every device of a user.
- Deadline reminders run hourly inside the existing worker thread loop:
  reminder_iso <= now+48h and not yet reminded → push + in-app notification.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from app.core.config import settings
from app.db.schema import get_db

log = logging.getLogger("rv.push")

_VAPID_PATH = Path(settings.data_dir) / "push_vapid.json"


def get_vapid() -> dict:
    if _VAPID_PATH.exists():
        return json.loads(_VAPID_PATH.read_text(encoding="utf-8"))
    try:
        # generate a P-256 keypair directly with cryptography (py_vapid's
        # private_key() returns an object, not PEM, in newer versions)
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization

        key = ec.generate_private_key(ec.SECP256R1())
        private_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        pub_bytes = key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint)
        import base64

        application_server_key = base64.urlsafe_b64encode(
            pub_bytes).rstrip(b"=").decode()
        data = {"private_key": private_pem,
                "public_key": application_server_key,
                "subject": settings.vapid_subject}
        _VAPID_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log.info("generated VAPID keys at %s", _VAPID_PATH)
        return data
    except ImportError:
        log.warning("cryptography not installed — push disabled")
        return {}


def vapid_public_key() -> str:
    return get_vapid().get("public_key", "")


def save_subscription(user_id: int, endpoint: str, p256dh: str,
                      auth: str, user_agent: str = "") -> bool:
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO push_subscriptions(user_id, endpoint, p256dh,"
                " auth, user_agent) VALUES (?,?,?,?,?)"
                " ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id,"
                " p256dh=excluded.p256dh, auth=excluded.auth",
                (user_id, endpoint, p256dh, auth, user_agent[:200]))
        return True
    except Exception as e:  # noqa: BLE001
        log.error("subscription save failed: %s", e)
        return False


def remove_subscription(endpoint: str) -> None:
    with get_db() as db:
        db.execute("DELETE FROM push_subscriptions WHERE endpoint=?",
                   (endpoint,))


def notify_user(user_id: int, title: str, body: str,
                url: str = "/", tag: str = "reelvault") -> int:
    """Send a push to all devices of a user. Returns delivered count."""
    subs = []
    with get_db() as db:
        subs = [dict(r) for r in db.execute(
            "SELECT * FROM push_subscriptions WHERE user_id=?", (user_id,))]
        db.execute(
            "INSERT INTO notifications(user_id, kind, title, body)"
            " VALUES (?, 'push', ?, ?)", (user_id, title[:200], body[:500]))
    if not subs:
        return 0
    vapid = get_vapid()
    if not vapid:
        return 0
    sent = 0
    from pywebpush import webpush, WebPushException

    for s in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": s["endpoint"],
                    "keys": {"p256dh": s["p256dh"], "auth": s["auth"]},
                },
                data=json.dumps({"title": title, "body": body, "url": url,
                                 "tag": tag}),
                vapid_private_key=vapid["private_key"],
                vapid_claims={"sub": f"mailto:{settings.vapid_subject}"},
            )
            sent += 1
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            if code in (404, 410):   # subscription expired/gone
                remove_subscription(s["endpoint"])
                log.info("pruned dead subscription %s…", s["endpoint"][:50])
            else:
                log.warning("push failed (%s): %s", code, str(e)[:120])
        except Exception as e:  # noqa: BLE001
            log.warning("push error: %s", str(e)[:120])
    return sent


# ------------------------------------------------------------- deadlines
_reminded: set[int] = set()


def scan_deadlines() -> int:
    """Push reminders for reels whose reminder_iso is within 48h."""
    import datetime as dt

    now = dt.datetime.now()
    horizon = now + dt.timedelta(hours=48)
    sent = 0
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT id, user_id, title, reminder_iso FROM reels"
            " WHERE reminder_iso IS NOT NULL AND status='completed'")]
    for r in rows:
        if r["id"] in _reminded:
            continue
        try:
            rem = dt.datetime.fromisoformat(r["reminder_iso"])
        except Exception:
            continue
        if rem <= horizon:
            ok = notify_user(
                r["user_id"],
                f"⏰ Deadline approaching ({(rem - now).days}d)",
                f"{r['title'] or 'A saved reel'} — reminder due "
                f"{rem.strftime('%b %d')}",
                url=f"/?open={r['id']}", tag=f"deadline-{r['id']}")
            _reminded.add(r["id"])
            sent += 1 if ok >= 0 else 0
    return sent


def start_scanner(every_s: int = 3600) -> None:
    import threading

    def loop():
        while True:
            try:
                scan_deadlines()
            except Exception as e:  # noqa: BLE001
                log.warning("deadline scan failed: %s", e)
            time.sleep(every_s)

    threading.Thread(target=loop, daemon=True, name="rv-deadlines").start()
