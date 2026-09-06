"""Business-SLM provider — ReelVault's own vendored mini-transformer
(vendored source: vendor/slm, weights: models/biz_slm.pt).

Fully self-contained on D:/reelvault — no external project references.
Honest status (measured Aug 26): the checkpoint is a 6-step smoke train
(val ppl ~218) producing degenerate output; the ROUTER therefore keeps it
OFF by default. Enable via RV_SLM_ENABLED=true after real fine-tuning;
the benchmark harness will measure whether it beats Qwen.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

from app.ai.providers import _record_run
from app.core.config import settings

log = logging.getLogger("rv.slm")

APP_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CHECKPOINT = str(APP_ROOT / "models" / "biz_slm.pt")
TOKENIZER_DIR = str(APP_ROOT / "models" / "biz_slm_tokenizer")
SLM_SRC = str(APP_ROOT / "vendor")


class BusinessSLMProvider:
    """Chat-shaped shim over CPURuntime.generate_batch (greedy).

    The SLM was trained on 'Text: ... JSON:' style prompts, so chat messages
    are flattened into that format. Output parsing extracts the first JSON
    object; non-JSON output returns '' so callers treat it as a miss and the
    router falls back to Qwen.
    """

    name = "business-slm"

    def __init__(self, checkpoint: str | None = None):
        self._rt = None
        self._tok = None
        self._lock = threading.Lock()
        self.checkpoint = checkpoint or DEFAULT_CHECKPOINT

    # ---- lifecycle ---------------------------------------------------
    def available(self) -> bool:
        try:
            self._load()
            return True
        except Exception as e:  # noqa: BLE001
            log.debug("slm unavailable: %s", e)
            return False

    def _load(self):
        if self._rt is None:
            import sys

            if SLM_SRC not in sys.path:
                sys.path.insert(0, SLM_SRC)
            from slm.inference.runtime import CPURuntime

            t0 = time.time()
            rt = CPURuntime(self.checkpoint, warmup=False)
            if settings.slm_quantize:
                try:
                    rt.quantize_int8()  # 20.7MB -> ~1MB weights
                    log.info("slm int8 quantized")
                except Exception as e:  # noqa: BLE001
                    log.warning("slm int8 failed (%s); running fp32", e)
            rt._rt_loaded_at = time.time() - t0
            from slm.tokenizer.bpe import BPETokenizer

            self._tok = BPETokenizer.load(TOKENIZER_DIR)
            self._rt = rt
            n = sum(p.numel() for p in rt.model.parameters())
            log.info("business-slm loaded: %.2fM params from %s (%.1fs)",
                     n / 1e6, self.checkpoint, rt._rt_loaded_at)
        return self._rt

    # ---- LLMProvider contract ---------------------------------------
    def chat(self, messages: list[dict], max_tokens: int = 160,
             temperature: float = 0.0, json_mode: bool = False,
             reel_id: int | None = None) -> str:
        rt = self._load()
        prompt = self._flatten(messages)
        ids = rt.encode_prompt_ids(self._tok, prompt)
        t0 = time.time()
        try:
            with self._lock:  # single-threaded generation guard
                out = rt.generate_batch([ids], max_new_tokens=max_tokens,
                                        temperature=0.0)[0].tolist()
        except Exception as e:  # noqa: BLE001
            _record_run("llm.chat", self.name, "biz-slm-5.3M", t0, False,
                        reel_id=reel_id, error=str(e))
            raise
        text = self._decode(out)
        _record_run("llm.chat", self.name, "biz-slm-5.3M", t0, True,
                    reel_id=reel_id, tokens_out=len(out))
        return self._extract_json(text) if json_mode else text.strip()

    # ---- helpers ------------------------------------------------------
    @staticmethod
    def _flatten(messages: list[dict]) -> str:
        sys_txt = " ".join(m["content"] for m in messages if m["role"] == "system")
        usr_txt = " ".join(m["content"] for m in messages if m["role"] == "user")
        return f"{sys_txt}\nText: {usr_txt}\nJSON:".strip()

    def _decode(self, ids: list[int]) -> str:
        self._load()
        return self._tok.decode([i for i in ids if i > 0])

    @staticmethod
    def _extract_json(text: str) -> str:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return ""
        raw = m.group(0)
        try:
            json.loads(raw)
            return raw
        except Exception:
            return ""


def get_slm() -> BusinessSLMProvider:
    global _slm
    try:
        return _slm  # type: ignore[name-defined]
    except NameError:
        pass
    globals()["_slm"] = BusinessSLMProvider()
    return globals()["_slm"]
