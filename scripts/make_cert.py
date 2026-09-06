"""Self-signed certificate generator for LAN/HTTPS access.

Web Crypto APIs (service worker, push, share-target on iOS) require a secure
context. For personal use, a self-signed cert for your LAN IP works — phones
need to trust it once. For valid certs with zero config, prefer Tailscale
(see docs/DEPLOYMENT.md) or Cloudflare Tunnel.
"""
from __future__ import annotations

import datetime
import ipaddress
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402


def _lan_ips() -> list[str]:
    ips = {"localhost", "127.0.0.1"}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # no traffic actually sent
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(ips)


def generate_cert(out_dir: Path | None = None, days: int = 825) -> dict:
    out_dir = Path(out_dir or (settings.data_dir / "certs"))
    out_dir.mkdir(parents=True, exist_ok=True)
    key_pem = out_dir / "key.pem"
    cert_pem = out_dir / "cert.pem"
    if cert_pem.exists() and key_pem.exists():
        return {"cert": str(cert_pem), "key": str(key_pem),
                "existed": True}

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ReelVault")])
    san_items = []
    for ip in _lan_ips():
        if ip == "localhost":
            san_items.append(x509.DNSName("localhost"))
            continue
        try:
            san_items.append(x509.IPAddress(ipaddress.IPv4Address(ip)))
        except ValueError:
            san_items.append(x509.DNSName(ip))
    san_items.append(x509.DNSName("reelvault.local"))
    san = x509.SubjectAlternativeName(san_items)
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)   # self-signed
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(san, critical=False)
        .sign(key, hashes.SHA256()))

    key_pem.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    cert_pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return {"cert": str(cert_pem), "key": str(key_pem), "ips": _lan_ips(),
            "existed": False}


def run_https(port: int | None = None):
    """Serve the app over HTTPS using (generating if needed) local certs."""
    paths = generate_cert()
    port = port or settings.port
    import uvicorn

    uvicorn.run("app.api.main:app", host="0.0.0.0", port=port,
                ssl_certfile=paths["cert"], ssl_keyfile=paths["key"],
                log_level="warning")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    info = generate_cert()
    print("certificate:", info["cert"])
    print("private key:", info["key"])
    print("covers IPs :", ", ".join(_lan_ips()))
    print("\nPhone trust: open https://<LAN-IP>:8756 → accept warning, or")
    print("install cert.pem into Trusted Root (one time).")
