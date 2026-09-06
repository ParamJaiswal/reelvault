# Deployment & Remote Access (HTTPS)

## Option A — Tailscale (RECOMMENDED for personal use)

Zero port-forwarding, valid HTTPS certs, works from anywhere:

1. Install Tailscale on the PC: https://tailscale.com/download → log in
2. Install Tailscale on your phone, same account
3. Enable HTTPS certs (one time, on the PC):
   ```powershell
   tailscale cert   # follow prompts to enable MagicDNS + HTTPS
   ```
4. Run ReelVault behind it:
   ```powershell
   tailscale serve --bg https+insecure://127.0.0.1:8756
   ```
5. Phone: open `https://<machine-name>.<tailnet>.ts.net` — valid cert,
   install the PWA, share reels from Instagram anywhere you are.

## Option B — Cloudflare Tunnel (you own a domain)

```powershell
cloudflared tunnel login
cloudflared tunnel create reelvault
cloudflared tunnel route dns reelvault rv.yourdomain.com
cloudflared tunnel run --url http://127.0.0.1:8756 reelvault
```
Valid public cert automatically; keep the token secret.

## Option C — Self-signed certificate (fallback only)

> Prefer A or B. Self-signed means browser warnings, manual trust, and no
> Web Push on some browsers. Use only when Tailscale/CF are impossible.

```powershell
cd D:\reelvault
.venv\Scripts\python.exe scripts\make_cert.py     # generates data/certs/*
# serve over HTTPS:
.venv\Scripts\python.exe -c "from scripts.make_cert import run_https; run_https()"
```
Open `https://<PC-IP>:8756` on the phone and accept the self-signed warning
once (or install `data/certs/cert.pem` into Trusted Root). Web Push and PWA
install work after that.

> Never expose raw port 8756 to the internet without one of the above.
