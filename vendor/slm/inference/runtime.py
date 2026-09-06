"""CPU-first inference runtime.

Loads ``.pt`` checkpoints produced by the Trainer (model + model_config
payload), keeps everything on CPU, and offers optional INT8 dynamic
quantization of all ``nn.Linear`` layers to shrink RAM and speed up
dense-matmul-bound generation on x86.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from slm.model.config import ModelConfig
from slm.model.model import TransformerLM
from slm.tokenizer.bpe import EOS


def ram_rss_mb() -> float:
    """Resident memory of this process in MB (psutil-based)."""
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except ImportError:  # pragma: no cover - psutil is a dev extra
        import resource  # noqa: F401  (unix fallback)

        return -1.0


class CPURuntime:
    """Owns a TransformerLM on CPU; optional INT8 dynamic quantization."""

    def __init__(
        self,
        checkpoint: str | Path,
        *,
        quantize: bool = False,
        threads: int | None = None,
        warmup: bool = True,
    ) -> None:
        self.checkpoint_path = Path(checkpoint)
        if not self.checkpoint_path.exists():
            raise FileNotFoundError(f"checkpoint not found: {self.checkpoint_path}")
        if torch.cuda.is_available():
            print("warning: CUDA visible but runtime is CPU-only by design")

        payload = torch.load(self.checkpoint_path, map_location="cpu", weights_only=False)
        model_config = payload.get("model_config") or {}
        self.cfg = ModelConfig(**model_config) if model_config else ModelConfig()
        self.model = TransformerLM(self.cfg)
        missing, unexpected = self.model.load_state_dict(payload["model"], strict=False)
        if missing or unexpected:
            raise RuntimeError(
                f"checkpoint mismatch: missing={list(missing)} unexpected={list(unexpected)}"
            )
        self.model.eval()
        self.quantized = False
        self.load_seconds = 0.0  # set externally if desired

        if threads:
            torch.set_num_threads(threads)
        if quantize:
            self.quantize_int8()
        if warmup:
            self._warmup()

    # ------------------------------------------------------------------ setup

    def quantize_int8(self) -> None:
        """INT8 dynamic quantization targeting nn.Linear (configurable toggle)."""
        qmodel = torch.ao.quantization.quantize_dynamic(
            self.model, {nn.Linear}, dtype=torch.qint8
        )
        self.model = qmodel
        self.quantized = True

    def _warmup(self) -> None:
        """Prime kernels so first real request is not penalized."""
        ids = torch.tensor([[6, 7, 8]], dtype=torch.long)
        with torch.no_grad():
            self.model(ids)

    # ------------------------------------------------------------ introspection

    @property
    def num_params(self) -> int:
        return sum(p.numel() for p in self.model.parameters())

    def weights_bytes(self) -> int:
        """Approximate serialized size of model weights in bytes."""
        total = 0
        for buf in self.model.state_dict().values():
            if not isinstance(buf, torch.Tensor):
                continue  # quantized state dicts also carry dtype placeholders
            try:
                total += buf.numel() * buf.element_size()
            except Exception:
                total += buf.numel()
        return total

    # --------------------------------------------------------------- inference

    def encode_prompt_ids(self, tokenizer, prompt: str) -> list[int]:
        return tokenizer.encode(prompt)

    @torch.no_grad()
    def generate_ids(
        self,
        prompt_ids: list[int],
        *,
        max_new_tokens: int = 32,
        temperature: float = 0.0,
        top_k: int | None = None,
        eos_id: int = EOS,
    ) -> list[list[int]]:
        """Single-prompt generation; returns new-token ids per sequence."""
        cur = torch.tensor([prompt_ids], dtype=torch.long)
        out = self.model.generate(
            cur, max_new_tokens=max_new_tokens,
            temperature=temperature, top_k=top_k, eos_id=eos_id,
        )
        n = len(prompt_ids)
        return [out[i, n:].tolist() for i in range(out.shape[0])]

    @torch.no_grad()
    def generate_ids_iter(
        self,
        prompt_ids: list[int],
        *,
        max_new_tokens: int = 32,
        temperature: float = 0.0,
        top_k: int | None = None,
        eos_id: int = EOS,
    ):
        """Yield new-token ids one at a time (enables true TTFT measurement)."""
        cur = torch.tensor([prompt_ids], dtype=torch.long)
        was_training = self.model.training
        self.model.eval()
        try:
            for _ in range(max_new_tokens):
                out = self.model(cur[:, -self.cfg.max_position_embeddings:])
                logits = out.logits[:, -1, :]
                if temperature <= 0:
                    nid = torch.argmax(logits, dim=-1, keepdim=True)
                else:
                    logits = logits / temperature
                    if top_k is not None:
                        kth = torch.topk(logits, min(top_k, logits.size(-1)), dim=-1).values[..., -1:]
                        logits = logits.masked_fill(logits < kth, float("-inf"))
                    nid = torch.multinomial(F.softmax(logits, dim=-1), 1)
                cur = torch.cat((cur, nid), dim=1)
                tid = int(nid)
                yield tid
                if tid == eos_id:
                    break
        finally:
            if was_training:
                self.model.train()

    @torch.no_grad()
    def generate_batch(
        self,
        prompts_ids: list[list[int]],
        *,
        max_new_tokens: int = 32,
        temperature: float = 0.0,
    ) -> torch.Tensor:
        """Left-aligned batch generation (pads via pad_token_id=0)."""
        bsz = len(prompts_ids)
        length = max(len(p) for p in prompts_ids)
        cur = torch.full((bsz, length), self.cfg.pad_token_id, dtype=torch.long)
        for i, p in enumerate(prompts_ids):
            cur[i, length - len(p):] = torch.tensor(p, dtype=torch.long)
        return self.model.generate(cur, max_new_tokens=max_new_tokens, temperature=temperature)


def load_runtime(checkpoint: str | Path, **kwargs) -> CPURuntime:
    return CPURuntime(checkpoint, **kwargs)
