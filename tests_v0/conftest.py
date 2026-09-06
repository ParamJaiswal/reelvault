"""Runs before v0 imports — env must point at temp dirs, not real data."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="rv0test_"))
os.environ["RV0_DB_PATH"] = str(_TMP / "test.db")
os.environ["RV0_MEDIA_DIR"] = str(_TMP / "media")
