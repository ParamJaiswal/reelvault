"""AI provider abstraction layer.

Every capability is a protocol with a local-first implementation:
  LLMProvider        -> LlamaCppProvider (OpenAI-compatible llama-server)
  Transcriber        -> WhisperProvider (faster-whisper, CUDA->CPU fallback)
  OcrProvider        -> RapidOcrProvider (ONNX, CPU)
  EmbeddingProvider  -> FastembedProvider (bge-small-en-v1.5, ONNX, CPU)

All calls are timed and recorded into ai_runs (observability requirement).
Backends never touch business logic directly.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.core.config import APP_ROOT, settings
from app.db.schema import get_db

log = logging.getLogger("rv.ai")


# ----------------------------------------------------------------- helpers
def _record_run(task: str, backend: str, model: str, t0: float, ok: bool,
                reel_id: int | None = None, tokens_in: int | None = None,
                tokens_out: int | None = None, error: str | None = None) -> None:
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO ai_runs(reel_id, task, backend, model, latency_ms,"
                " tokens_in, tokens_out, ok, error) VALUES (?,?,?,?,?,?,?,?,?)",
                (reel_id or None, task, backend, model, int((time.time() - t0) * 1000),
                 tokens_in, tokens_out, int(ok), (error or "")[:500]),
            )
    except Exception:  # noqa: BLE001 - telemetry must never break the pipeline
        log.exception("ai_runs insert failed")


# ------------------------------------------------------------------- LLM
class LLMProvider(Protocol):
    name: str
    def chat(self, messages: list[dict], max_tokens: int = 700,
             temperature: float = 0.2, json_mode: bool = False) -> str: ...


class LlamaCppProvider:
    """Talks to llama-server's OpenAI-compatible endpoint."""
    name = "llamacpp"

    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.base_url = (base_url or settings.llm_server_url).rstrip("/")
        self.model = model or settings.llm_model_name

    def available(self) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/models", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def chat(self, messages: list[dict], max_tokens: int = 700,
             temperature: float = 0.2, json_mode: bool = False,
             reel_id: int | None = None) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        t0 = time.time()
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", json=body, timeout=600)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            usage = r.json().get("usage", {})
            _record_run("llm.chat", self.name, self.model, t0, True,
                        reel_id=reel_id, tokens_in=usage.get("prompt_tokens"),
                        tokens_out=usage.get("completion_tokens"))
            return text
        except Exception as e:  # noqa: BLE001
            _record_run("llm.chat", self.name, self.model, t0, False,
                        reel_id=reel_id, error=str(e))
            raise


# ------------------------------------------------------------ Transcription
class Transcriber(Protocol):
    name: str
    def transcribe(self, wav_path: str, reel_id: int | None = None) -> dict: ...


class WhisperProvider:
    """faster-whisper (CTranslate2) with CUDA -> CPU fallback."""

    def __init__(self):
        self._model = None
        self.name = f"faster-whisper:{settings.whisper_model_size}"

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._register_cuda_dlls()
            try:
                self._model = WhisperModel(
                    settings.whisper_model_size,
                    device=settings.whisper_device,
                    compute_type=settings.whisper_compute,
                )
                log.info("whisper loaded device=%s", settings.whisper_device)
            except Exception as e:  # noqa: BLE001
                log.warning("CUDA load failed (%s) -> cpu/int8", e)
                self._model = WhisperModel(
                    settings.whisper_model_size, device="cpu", compute_type="int8"
                )
        return self._model

    @staticmethod
    def _register_cuda_dlls() -> None:
        """ctranslate2 needs cublas64_12/cudart64_12; the bundled llama.cpp
        dir already ships them. Without this, the CUDA attempt fails AND the
        in-process cpu retry fails with it (DLL state is process-wide)."""
        import os

        llamacpp = APP_ROOT / "llamacpp"
        if llamacpp.is_dir() and any(llamacpp.glob("cublas64_*.dll")):
            os.add_dll_directory(str(llamacpp))
            os.environ.setdefault("PATH", f"{llamacpp};{os.environ.get('PATH', '')}")

    def transcribe(self, wav_path: str, reel_id: int | None = None) -> dict:
        model = self._load()
        t0 = time.time()
        try:
            segments, info = model.transcribe(
                wav_path, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 400},
                beam_size=1,
            )
            segs = [
                {
                    "start": round(s.start, 2),
                    "end": round(s.end, 2),
                    "text": s.text.strip(),
                    "avg_logprob": round(s.avg_logprob, 3),
                    "no_speech_prob": round(s.no_speech_prob, 3),
                }
                for s in segments
            ]
            return self._finish(segs, info.language, info.language_probability,
                                wav_path, reel_id, t0)
        except Exception as e:  # noqa: BLE001
            _record_run("transcribe", self.name, settings.whisper_model_size,
                        t0, False, reel_id=reel_id, error=str(e))
            raise

    def _finish(self, segs, language, lang_prob, wav_path, reel_id, t0):
        result = {
            "language": language,
            "language_probability": round(lang_prob, 3) if lang_prob else None,
            "segments": segs,
            "full_text": " ".join(s["text"] for s in segs).strip(),
        }
        _record_run("transcribe", self.name, settings.whisper_model_size,
                    t0, True, reel_id=reel_id, tokens_out=len(segs))
        return result


class TorchWhisperProvider:
    """OpenAI whisper (PyTorch). Fallback when CTranslate2 DLLs are blocked
    by Windows Application Control — same output contract."""

    def __init__(self):
        self._model = None
        self.name = f"whisper-torch:{settings.whisper_model_size}"

    def _load(self):
        if self._model is None:
            import torch
            import whisper

            device = ("cuda"
                      if settings.whisper_device == "cuda"
                      and torch.cuda.is_available() else "cpu")
            try:
                self._model = whisper.load_model(
                    settings.whisper_model_size, device=device)
                log.info("torch-whisper loaded size=%s device=%s",
                         settings.whisper_model_size, device)
            except Exception as e:  # noqa: BLE001
                log.warning("torch-whisper %s failed (%s) -> cpu",
                            device, e)
                self._model = whisper.load_model(
                    settings.whisper_model_size, device="cpu")
        return self._model

    def transcribe(self, wav_path: str, reel_id: int | None = None) -> dict:
        import torch

        model = self._load()
        t0 = time.time()
        try:
            result = model.transcribe(wav_path,
                                      fp16=torch.cuda.is_available())
            segs = [
                {
                    "start": round(s["start"], 2),
                    "end": round(s["end"], 2),
                    "text": s["text"].strip(),
                    "avg_logprob": round(s.get("avg_logprob", -0.3), 3),
                    "no_speech_prob": round(s.get("no_speech_prob", 0.0), 3),
                }
                for s in result.get("segments", [])
            ]
            return self._finish(segs, result.get("language", "en"), None,
                                wav_path, reel_id, t0)
        except Exception as e:  # noqa: BLE001
            _record_run("transcribe", self.name, settings.whisper_model_size,
                        t0, False, reel_id=reel_id, error=str(e))
            raise

    def _finish(self, segs, language, lang_prob, wav_path, reel_id, t0):
        result = {
            "language": language,
            "language_probability": lang_prob,
            "segments": segs,
            "full_text": " ".join(s["text"] for s in segs).strip(),
        }
        _record_run("transcribe", self.name, settings.whisper_model_size,
                    t0, True, reel_id=reel_id, tokens_out=len(segs))
        return result


# -------------------------------------------------------------------- OCR
class OcrProvider(Protocol):
    name: str
    def read_image(self, path: str) -> list[dict]: ...


class RapidOcrProvider:
    def __init__(self):
        self._engine = None
        self.name = "rapidocr-onnx"

    def _load(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR()
        return self._engine

    def read_image(self, path: str, reel_id: int | None = None) -> list[dict]:
        engine = self._load()
        t0 = time.time()
        try:
            result, _ = engine(path)
            items = []
            for box, text, conf in (result or []):
                items.append({"text": text.strip(), "conf": round(float(conf), 3)})
            _record_run("ocr", self.name, "v4", t0, True,
                        reel_id=reel_id, tokens_out=len(items))
            return items
        except Exception as e:  # noqa: BLE001
            _record_run("ocr", self.name, "v4", t0, False,
                        reel_id=reel_id, error=str(e))
            raise


# -------------------------------------------------------------- Embeddings
class EmbeddingProvider(Protocol):
    name: str
    dim: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastembedProvider:
    def __init__(self):
        self._model = None
        self.name = "fastembed:bge-small-en-v1.5"
        self.dim = 384

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding("BAAI/bge-small-en-v1.5",
                                        cache_dir=str(settings.models_dir / "fastembed"))
        return self._model

    def embed(self, texts: list[str], reel_id: int | None = None) -> list[list[float]]:
        model = self._load()
        t0 = time.time()
        try:
            vecs = [list(map(float, v)) for v in model.embed(texts)]
            _record_run("embed", self.name, "bge-small", t0, True,
                        reel_id=reel_id, tokens_in=sum(len(t) for t in texts))
            return vecs
        except Exception as e:  # noqa: BLE001
            _record_run("embed", self.name, "bge-small", t0, False,
                        reel_id=reel_id, error=str(e))
            raise


# ------------------------------------------------------------- singletons
_llm: LLMProvider | None = None
_transcriber: Transcriber | None = None
_ocr: OcrProvider | None = None
_embedder: EmbeddingProvider | None = None


def get_llm() -> LLMProvider:
    global _llm
    if _llm is None:
        _llm = LlamaCppProvider()
    return _llm


class AutoTranscriber:
    """Tries the fast backend; permanently switches to torch-whisper if the
    environment can't run it (missing/blocked CUDA DLLs etc.)."""

    def __init__(self):
        self._primary: Transcriber | None = None
        self._active: Transcriber | None = None

    @property
    def name(self) -> str:
        return self._active.name if self._active else "auto-transcriber"

    def _ensure(self):
        if self._active is None:
            self._primary = WhisperProvider()
            self._active = self._primary

    def transcribe(self, wav_path: str, reel_id: int | None = None) -> dict:
        self._ensure()
        try:
            return self._active.transcribe(wav_path, reel_id)
        except Exception as e:  # noqa: BLE001
            # Any primary failure (missing module, blocked DLL, CUDA issue)
            # triggers the one-time permanent switch to the torch backend.
            if self._active is self._primary:
                log.warning("transcriber '%s' failed (%s) -> torch fallback",
                            self._active.name, str(e)[:120])
                self._active = TorchWhisperProvider()
                return self._active.transcribe(wav_path, reel_id)
            raise


def get_transcriber() -> Transcriber:
    global _transcriber
    if _transcriber is None:
        backend = settings.whisper_backend
        if backend == "torch":
            _transcriber = TorchWhisperProvider()
        else:
            _transcriber = AutoTranscriber()
    return _transcriber


def get_ocr() -> OcrProvider:
    global _ocr
    if _ocr is None and settings.ocr_enabled:
        _ocr = RapidOcrProvider()
    return _ocr


def get_embedder() -> EmbeddingProvider:
    global _embedder
    if _embedder is None:
        _embedder = FastembedProvider()
    return _embedder
