# Session handoff — Production hardening (Aug 26, 2026, evening)

## Implemented & verified live
1. **Auth v2** (`app/core/auth.py`, migration v4): password logins
   (PBKDF2-240k), HS256 access JWTs (30 min), rotating refresh tokens stored
   HASHED as session CHAINS — old rows kept with revoked=2 so REUSE of a
   rotated token revokes the entire chain (theft response). Roles:
   owner>admin>viewer enforced per-endpoint. Bootstrap .auth_token still works
   as owner. First-run owner credentials: data/.owner_credentials.txt.
   Endpoints: /api/auth/login|refresh|logout|sessions(+DELETE),
   /api/admin/users. Verified live incl. theft test → family revoked.
2. **Web Push** (`app/core/push.py`): VAPID P-256 keypair auto-generated
   (cryptography lib directly — py_vapid not needed), push_subscriptions
   table, /api/push/public-key|subscribe|unsubscribe|test, SW push+click
   handlers in sw.js, client opt-in in push-client.js (HTTPS-only), hourly
   deadline scanner pushes ≤48h reminders. Live: publicKey 87-char valid.
3. **HTTPS**: scripts/make_cert.py self-signed cert covering LAN IP + localhost;
   verified uvicorn serves https://...:8443/8444 with 200s. docs/DEPLOYMENT.md
   documents Tailscale (recommended) + Cloudflare Tunnel + self-signed paths.
4. **Docker**: Dockerfile multi-stage CUDA build (llama.cpp compiled in
   builder stage; runtime = nvidia/cuda 12.4 runtime), Caddy sidecar for
   internal HTTPS on :8443, volumes for models/data/media, healthcheck.
   NOTE: Docker not installed on this PC yet — build untested locally.
5. **Backups** (`app/core/backups.py`): SQLite online-backup API snapshot +
   incremental media mirror with retention; Fernet encryption from
   RV_BACKUP_PASSPHRASE. POST /api/admin/backup (admin+). Daily scheduler
   thread started at boot (D:/reelvault-backups). Verified live: DB snapshot
   written + media mirror 105 files unchanged.

## Tests
36 passed (28 prior + 8 new prod tests covering hash roundtrip,
create/rotate/revoke/reuse-theft, garbage tokens, role rules, backup
plaintext+encrypted roundtrip, media mirror incrementality, VAPID gen).

## Notes / next
- Viewer DELETE returns 404 when reel missing — acceptable; could map to 403
  for stricter semantics.
- Push permission prompt fires once per browser after first click.
- For phone push over LAN: must use HTTPS path (self-signed accepted or
  Tailscale). Plain HTTP blocks Web Push by browser design.
- Dockerfile ready to build once Docker Desktop is installed.
