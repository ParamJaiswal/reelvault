"""v0 config — env-driven, repo-relative defaults. No hardcoded drives."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name) or default)


DB_PATH = _path("RV0_DB_PATH", ROOT / "data" / "v0.db")
MEDIA_DIR = _path("RV0_MEDIA_DIR", ROOT / "media" / "v0")
PORT = int(os.environ.get("RV0_PORT", "8760"))
HOST = os.environ.get("RV0_HOST", "127.0.0.1")
