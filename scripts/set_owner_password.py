"""Replace an unknown owner password and delete its plaintext record.

`ensure_owner_user` (`app/core/auth.py`) hashes a `token_urlsafe(10)` password
onto the first user and writes it in plaintext to `data/.owner_credentials.txt`
when it *promotes* an existing owner row — the upgrade path this database came
in by. (A truly empty `users` table takes the other branch and never writes the
file, so the generated password is stored nowhere and password login is
unusable.) Either way `warn_if_owner_credentials_stale` logs until the file is
gone, and no route changes a password: `/api/auth/*` stops at
login/refresh/logout/sessions. So the file was simultaneously the only copy of
the working credential and the thing that had to be removed.

The replacement is read with `getpass`: never on the command line (it would
land in shell history and the process list) and never echoed. The file is
unlinked only after the transaction carrying the new hash has committed, so a
refused password or a failed write leaves the recorded credential intact.

Usage:
    .venv/Scripts/python.exe scripts/set_owner_password.py
"""
from __future__ import annotations

import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.auth import hash_password  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.schema import get_db  # noqa: E402

MIN_PASSWORD_CHARS = 8  # same floor as auth.create_user


def set_owner_password(password: str) -> dict:
    """Hash `password` onto the owner row, then drop the credentials file.

    Raises LookupError when no owner exists and ValueError on a short password,
    so a rejected value never reaches the database.
    """
    if len(password) < MIN_PASSWORD_CHARS:
        raise ValueError(
            f"password must be at least {MIN_PASSWORD_CHARS} characters")
    pw_hash, salt = hash_password(password)
    with get_db() as db:
        row = db.execute("SELECT id, username FROM users WHERE role='owner'"
                         " ORDER BY id LIMIT 1").fetchone()
        if row is None:
            raise LookupError("no owner user in this database")
        db.execute("UPDATE users SET password_hash=?, password_salt=?"
                   " WHERE id=?", (pw_hash, salt, row["id"]))
    cred_file = settings.data_dir / ".owner_credentials.txt"
    had_file = cred_file.exists()
    cred_file.unlink(missing_ok=True)
    return {"username": row["username"], "file_removed": had_file}


def main(argv: list[str]) -> int:
    password = getpass("New owner password: ")
    if password != getpass("Repeat it: "):
        print("passwords did not match — nothing changed")
        return 1
    # Anything other than a designed refusal is a failure whose outcome is
    # unknown (did the commit land? did unlink run?), so let it raise rather
    # than print "nothing changed" next to a traceback that says otherwise.
    result = set_owner_password(password)
    print(f"owner '{result['username']}' password updated.")
    print("credentials file deleted." if result["file_removed"]
          else "no credentials file was present.")
    print("Existing API sessions keep working (they are token-based, not "
          "password-based) — revoke them in the UI if the old password spread.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
