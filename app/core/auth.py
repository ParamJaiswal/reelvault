"""Revocable token auth with rotating refresh tokens + roles.

Design:
- Bootstrap: the local .auth_token remains valid as 'owner' forever (keeps the
  single-user/local experience unchanged).
- Login (username+password) creates a session: short-lived access JWT + a
  long-lived opaque refresh token. BOTH are stored hashed (sha256) — a DB
  leak never yields usable tokens.
- Refresh rotates: old refresh is marked used; presenting an already-used
  refresh token = theft signal → the whole session family is revoked.
- Roles: owner > admin > viewer. Viewers read-only; admins manage content;
  only owner manages accounts/sessions.

Passwords: PBKDF2-HMAC-SHA256, 240k iterations, per-user salt, constant-time
verify. No plaintext anywhere.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.db.schema import get_db

log = logging.getLogger("rv.auth")

ACCESS_TTL_S = 30 * 60            # 30 min
REFRESH_TTL_S = 30 * 24 * 3600    # 30 days
PBKDF2_ITER = 240_000

_ROLES = ("owner", "admin", "viewer")
_ROLE_RANK = {"viewer": 0, "admin": 1, "owner": 2}


# ------------------------------------------------------------------ crypto
def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITER)
    return base64.b64encode(dk).decode(), base64.b64encode(salt).decode()


def verify_password(password: str, pw_hash: str, salt_b64: str) -> bool:
    try:
        salt = base64.b64decode(salt_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITER)
        return hmac.compare_digest(dk, base64.b64decode(pw_hash))
    except Exception:
        return False


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ------------------------------------------------------- JWT (HS256, tiny)

def _secret() -> bytes:
    secret_file = Path(settings.data_dir) / ".jwt_secret"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    return secret_file.read_text(encoding="utf-8").strip().encode()


def make_access_jwt(user_id: int, role: str, sid: int,
                    ttl_s: int = ACCESS_TTL_S) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({
        "sub": user_id, "role": role, "sid": sid,
        "exp": int(time.time()) + ttl_s,
        "jti": secrets.token_hex(8),
    }).encode())
    signing = f"{header}.{payload}".encode()
    sig = hmac.new(_secret(), signing, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(sig)}"


def decode_access_jwt(token: str) -> dict[str, Any] | None:
    try:
        h, p, s = token.split(".")
        signing = f"{h}.{p}".encode()
        expect = _b64url(hmac.new(_secret(), signing, hashlib.sha256).digest())
        if not hmac.compare_digest(s, expect):
            return None
        payload = json.loads(_b64url_dec(p))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ------------------------------------------------------------------ users
def ensure_owner_user() -> dict:
    """Fresh installs get a first owner with auto-generated credentials."""
    with get_db() as db:
        row = db.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
        if row is None:
            pw = secrets.token_urlsafe(10)
            pw_hash, salt = hash_password(pw)
            cur = db.execute(
                "INSERT INTO users(username, display_name, api_key_hash,"
                " role, password_hash, password_salt)"
                " VALUES ('owner','Owner','', 'owner', ?, ?)",
                (pw_hash, salt))
            uid = cur.lastrowid
            row = dict(db.execute("SELECT * FROM users WHERE id=?",
                                  (uid,)).fetchone())
            db.execute(
                "INSERT INTO ingestion_sources(user_id, kind, label)"
                " VALUES (?, 'share_target', 'PWA share target'),"
                " (?, 'url', 'Paste URL'), (?, 'watch_folder', 'Watch folder')",
                (uid, uid, uid))
            log.warning("fresh install: created first owner user %s", uid)
        if row["role"] == "owner" and row["password_hash"]:
            return dict(row)
        pw = secrets.token_urlsafe(10)
        pw_hash, salt = hash_password(pw)
        db.execute(
            "UPDATE users SET role='owner', password_hash=?, password_salt=?"
            " WHERE id=?", (pw_hash, salt, row["id"]))
        cred_file = settings.data_dir / ".owner_credentials.txt"
        cred_file.write_text(
            f"ReelVault owner login\nusername: {row['username']}\n"
            f"password: {pw}\n(change it after first login)\n", encoding="utf-8")
        log.warning("owner credentials written to %s", cred_file)
        return dict(db.execute("SELECT * FROM users WHERE id=?",
                               (row["id"],)).fetchone())


def create_user(username: str, password: str, role: str,
                display_name: str = "") -> dict | None:
    if role not in _ROLES or role == "owner":
        return None
    username = username.strip().lower()
    if not username or len(password) < 8:
        return None
    pw_hash, salt = hash_password(password)
    try:
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO users(username, display_name, api_key_hash,"
                " role, password_hash, password_salt)"
                " VALUES (?,?,?,?,?,?)",
                (username, display_name or username, "", role, pw_hash, salt))
            uid = cur.lastrowid
        return {"user_id": uid, "username": username, "role": role}
    except Exception:
        return None


def authenticate(username: str, password: str) -> dict | None:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM users WHERE username=? AND password_hash IS NOT NULL",
            ((username or "").strip().lower(),)).fetchone()
    if not row or not verify_password(password, row["password_hash"],
                                      row["password_salt"]):
        return None
    return {"user_id": row["id"], "username": row["username"],
            "role": row["role"]}


# --------------------------------------------------------------- sessions
def create_session(user_id: int, role: str, name: str = "") -> dict:
    now = time.time()
    with get_db() as db:
        if not db.execute("SELECT 1 FROM users WHERE id=?",
                          (user_id,)).fetchone():
            raise ValueError(f"user {user_id} does not exist")
    access = make_access_jwt(user_id, role, 0)      # sid patched below
    refresh = secrets.token_urlsafe(40)
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO auth_sessions(user_id, name, role, access_hash,"
            " refresh_hash, access_exp, refresh_exp)"
            " VALUES (?,?,?,?,?,?,?)",
            (user_id, name[:60], role, hash_token(access), hash_token(refresh),
             now + ACCESS_TTL_S, now + REFRESH_TTL_S))
        sid = cur.lastrowid
        # re-issue access bound to the real session id
        access = make_access_jwt(user_id, role, sid)
        db.execute("UPDATE auth_sessions SET access_hash=? WHERE id=?",
                   (hash_token(access), sid))
    return {"session_id": sid, "access_token": access,
            "refresh_token": refresh,
            "access_expires_in": ACCESS_TTL_S,
            "refresh_expires_in": REFRESH_TTL_S}


def _revoke_family(db, root_id: int) -> None:
    """Kill every session in a rotation chain."""
    db.execute(
        "UPDATE auth_sessions SET revoked=1"
        " WHERE id=? OR rotated_from=?"
        " OR id IN (SELECT id FROM auth_sessions WHERE rotated_from=?)"
        " OR id IN (SELECT rotated_from FROM auth_sessions WHERE id=?"
        "           AND rotated_from IS NOT NULL)",
        (root_id, root_id, root_id, root_id))


def rotate_session(refresh_token: str) -> dict | None:
    """Rotate a valid refresh token into a new child session.

    Old row kept with revoked=2 ('rotated away'): its hash still matches so
    a REUSED old token is detected → entire chain revoked (theft response).
    """
    rh = hash_token(refresh_token)
    now = time.time()
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM auth_sessions WHERE refresh_hash=?", (rh,)
        ).fetchone()
        if row is None:
            return None
        if row["revoked"] == 2:
            # REUSE of an already-rotated token => treat as theft
            log.warning("refresh reuse detected at session %s — revoking family",
                        row["rotated_from"] or row["id"])
            _revoke_family(db, row["rotated_from"] or row["id"])
            return None
        if row["revoked"] == 1 or row["refresh_exp"] < now:
            return None

        root = row["rotated_from"] or row["id"]
        new_refresh = secrets.token_urlsafe(40)
        # child session carries the live identity going forward
        cur = db.execute(
            "INSERT INTO auth_sessions(user_id, name, role, access_hash,"
            " refresh_hash, access_exp, refresh_exp, rotated_from)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (row["user_id"], row["name"], row["role"], "",
             hash_token(new_refresh), now + ACCESS_TTL_S,
             min(now + REFRESH_TTL_S, row["refresh_exp"]), root))
        child_id = cur.lastrowid
        access = make_access_jwt(row["user_id"], row["role"], child_id)
        db.execute("UPDATE auth_sessions SET access_hash=? WHERE id=?",
                   (hash_token(access), child_id))
        # retire the parent: refresh dead, hash retained for reuse detection
        db.execute("UPDATE auth_sessions SET revoked=2 WHERE id=?", (row["id"],))
        return {"access_token": access, "refresh_token": new_refresh,
                "access_expires_in": ACCESS_TTL_S,
                "role": row["role"], "user_id": row["user_id"],
                "session_id": child_id}


def revoke_session(session_id: int, user_id: int, require_role: str) -> bool:
    with get_db() as db:
        if require_role == "owner":
            cur = db.execute("UPDATE auth_sessions SET revoked=1 WHERE id=?",
                             (session_id,))
        else:
            cur = db.execute(
                "UPDATE auth_sessions SET revoked=1 WHERE id=? AND user_id=?",
                (session_id, user_id))
        return cur.rowcount > 0


def list_sessions(user_id: int | None = None) -> list[dict]:
    q = ("SELECT id, user_id, name, role, revoked, rotated_from,"
         " datetime(created_at) created, datetime(last_used) last_used,"
         " CAST(access_exp AS INTEGER)-STRFTIME('%s','now') access_left,"
         " CAST(refresh_exp AS INTEGER)-STRFTIME('%s','now') refresh_left"
         " FROM auth_sessions")
    args: tuple = ()
    if user_id is not None:
        q += " WHERE user_id=?"
        args = (user_id,)
    q += " ORDER BY id DESC LIMIT 100"
    with get_db() as db:
        return [dict(r) for r in db.execute(q, args)]


def resolve_request(token: str) -> dict | None:
    """Resolve any presented bearer token to {user_id, role}.

    Order: bootstrap token (owner) → live access JWT bound to unrevoked
    session → raw refresh token (allows CLI convenience).
    """
    if not token:
        return None
    from app.core.config import AUTH_TOKEN

    if hmac.compare_digest(token, AUTH_TOKEN):
        with get_db() as db:
            row = db.execute(
                "SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        if row is None:
            log.warning("bootstrap token presented but users table is empty")
            return None
        return {"user_id": row["id"], "role": "owner"}
    payload = decode_access_jwt(token)
    if payload:
        with get_db() as db:
            sess = db.execute(
                "SELECT revoked FROM auth_sessions WHERE id=?",
                (payload.get("sid", -1),)).fetchone()
        if sess and not sess["revoked"]:
            return {"user_id": payload["sub"], "role": payload["role"]}
        return None
    # raw refresh token convenience (hashed lookup, must be live)
    with get_db() as db:
        row = db.execute(
            "SELECT user_id, role, revoked, refresh_exp FROM auth_sessions"
            " WHERE refresh_hash=?", (hash_token(token),)).fetchone()
    if row and not row["revoked"] and row["refresh_exp"] >= time.time():
        return {"user_id": row["user_id"], "role": row["role"]}
    return None
