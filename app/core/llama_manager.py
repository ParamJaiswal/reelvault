"""Auto-manages the llama.cpp server: spawns it on startup if not reachable,
stops it cleanly on process exit. Makes ReelVault a true one-command app."""
from __future__ import annotations

import logging
import socket
import subprocess
import threading
import time
from pathlib import Path

from app.core.config import settings

log = logging.getLogger("rv.llama_mgr")

_proc: subprocess.Popen | None = None
CREATE_NO_WINDOW = 0x08000000  # windows: no console flash


def _server_base() -> tuple[str, int]:
    base = settings.llm_server_url.rstrip("/").split("/v1")[0]
    host = base.split("//")[1].split(":")[0]
    port = int(base.rsplit(":", 1)[1])
    return host, port


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _paths() -> tuple[Path, Path]:
    root = settings.models_dir.parent          # D:/reelvault
    return (root / "llamacpp" / "llama-server.exe",
            settings.models_dir / "qwen2.5-3b-instruct-q4_k_m.gguf")


def ensure_llama_server(wait_s: int = 240) -> None:
    """Called in a background thread at app startup."""
    global _proc
    if not settings.auto_start_llama:
        return
    host, port = _server_base()
    if _port_open(host, port):
        log.info("llama-server already running on %s:%s", host, port)
        return
    exe, model = _paths()
    if not exe.exists():
        log.warning("llama-server.exe not found at %s — LLM disabled", exe)
        return
    if not model.exists():
        log.warning("GGUF model not found at %s — LLM disabled", model)
        return
    logfile = Path(settings.data_dir) / "llama-server.log"
    log.info("spawning llama-server (log: %s)", logfile)
    try:
        with logfile.open("ab") as lf:
            _proc = subprocess.Popen(
                [str(exe), "-m", str(model),
                 "--host", host, "--port", str(port),
                 "-c", str(settings.llm_ctx_tokens), "-ngl", "33",
                 "--jinja", "--alias", settings.llm_model_name],
                stdout=lf, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW)
    except Exception as e:  # noqa: BLE001
        log.error("failed to spawn llama-server: %s", e)
        return
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if _proc.poll() is not None:
            log.error("llama-server exited early (code %s)", _proc.returncode)
            return
        if _port_open(host, port):
            log.info("llama-server is up (pid %s)", _proc.pid)
            return
        time.sleep(2)
    log.warning("llama-server still loading after %ss", wait_s)


def start_async() -> None:
    threading.Thread(target=ensure_llama_server, daemon=True,
                     name="llama-starter").start()


def shutdown() -> None:
    global _proc
    if _proc is not None and _proc.poll() is None:
        log.info("stopping llama-server (pid %s)", _proc.pid)
        try:
            _proc.terminate()
            _proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            _proc.kill()
    _proc = None
