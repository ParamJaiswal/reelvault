"""Central configuration via environment (.env supported)."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic_settings import BaseSettings

APP_ROOT = Path(__file__).resolve().parent.parent.parent

log = logging.getLogger("rv.config")


class Settings(BaseSettings):
    # --- storage ---
    data_dir: Path = APP_ROOT / "data"
    models_dir: Path = APP_ROOT / "models"
    media_dir: Path = APP_ROOT / "media"
    db_path: Path = APP_ROOT / "data" / "reelvault.db"

    # --- local model runtime ---
    llm_backend: str = "llamacpp"  # llamacpp | openai_compat | none
    llm_server_url: str = "http://127.0.0.1:8091/v1"
    llm_model_name: str = "qwen2.5-3b-instruct"
    llm_api_key: str = ""           # RV_LLM_API_KEY — openai_compat only; never log
    llm_ctx_tokens: int = 8192
    llm_max_tokens: int = 1400
    whisper_model_size: str = "small"  # tiny/base/small on 4GB VRAM
    whisper_backend: str = "auto"      # auto | torch
    whisper_device: str = "cuda"       # cuda | cpu
    whisper_compute: str = "float16"   # float16 on cuda, int8 on cpu
    embedding_backend: str = "fastembed"  # fastembed | none
    ocr_enabled: bool = True

    # --- business SLM (overnight-trained 5.3M transformer) ---
    slm_enabled: bool = False          # opt-in after benchmarking
    slm_checkpoint: str = ""           # empty -> built-in default path
    slm_quantize: bool = False         # INT8 dynamic quantization

    # --- one-command app experience ---
    auto_start_llama: bool = True      # spawn llama-server on app startup

    # --- auth & push ---
    vapid_subject: str = "reelvault@localhost"   # mailto for VAPID claims
    access_token_ttl_min: int = 30
    enable_registration: bool = True             # owner can create users via UI/API

    # --- backups ---
    backup_dir: str = str(APP_ROOT / "backups")
    backup_encrypt: bool = True
    backup_passphrase: str = ""        # set RV_BACKUP_PASSPHRASE to encrypt
    backup_keep: int = 7               # snapshots to retain

    # --- pipeline ---
    max_retries: int = 2
    retry_backoff_s: int = 5
    frame_sample_interval_s: float = 1.5
    max_frames_per_reel: int = 12
    worker_poll_interval_s: float = 1.0
    stale_job_timeout_s: int = 1800

    # --- server ---
    host: str = "127.0.0.1"
    port: int = 8756
    auth_token: str = ""              # empty -> auto-generate & persist
    cors_origins: str = ("http://127.0.0.1:8756,http://localhost:8756")

    # --- privacy defaults ---
    retention_media_days: int = 30    # 0 = keep forever
    default_keep_transcript: bool = True

    class Config:
        env_file = APP_ROOT / ".env"
        env_prefix = "RV_"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
(settings.media_dir / "audio").mkdir(parents=True, exist_ok=True)
(settings.media_dir / "frames").mkdir(parents=True, exist_ok=True)
(settings.media_dir / "video").mkdir(parents=True, exist_ok=True)


def get_auth_token() -> str:
    """Local-first auth: token generated once, stored in data/.auth_token."""
    if settings.auth_token:
        return settings.auth_token
    token_file = settings.data_dir / ".auth_token"
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip()
    import secrets

    tok = secrets.token_urlsafe(24)
    token_file.write_text(tok, encoding="utf-8")
    return tok


AUTH_TOKEN = get_auth_token()


def validate_startup_paths() -> None:
    """Fail fast with a clear message when required paths are unusable."""
    problems: list[str] = []
    warnings: list[str] = []

    for label, path in (("data", settings.data_dir),
                        ("media", settings.media_dir)):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".rv_write_probe"
            probe.touch()
            probe.unlink()
        except OSError as e:
            problems.append(f"{label} dir not writable: {path} ({e})")
    try:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        problems.append(f"DB dir not creatable: {settings.db_path.parent} ({e})")

    if not settings.models_dir.exists():
        warnings.append(
            f"models dir missing: {settings.models_dir} — set RV_MODELS_DIR "
            "or extraction will fail")

    if problems:
        raise RuntimeError("Startup path validation failed:\n- "
                           + "\n- ".join(problems))
    for w in warnings:
        log.warning("%s", w)


def warn_if_owner_credentials_stale() -> None:
    cred_file = settings.data_dir / ".owner_credentials.txt"
    if cred_file.exists():
        log.warning(
            "Owner credentials file still present (%s) — the password may "
            "still be the auto-generated one. Change it, then delete the "
            "file.", cred_file)
