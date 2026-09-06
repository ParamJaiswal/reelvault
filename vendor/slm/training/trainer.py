"""Stateful, resumable training engine.

Design goals:
- Dataset-agnostic: works with any torch ``Dataset`` of ``(input_ids, labels)``
  batches, so it is reusable for pretraining, domain-adaptation, and SFT.
- Config-driven hyperparameters with gradient accumulation and grad clipping.
- Cosine LR schedule with linear warmup.
- BF16/FP16 autocast mixed precision where supported by the device.
- Crash-safe checkpoints (.pt) capturing model/optimizer/scheduler/RNG state.
- JSON-lines experiment tracking; evaluation hooks run on a step or epoch cadence.
"""

from __future__ import annotations

import contextlib
import json
import math
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

from slm.evaluation.metrics import lm_loss_and_perplexity

_AMP_DTYPES = {"bf16": torch.bfloat16, "fp16": torch.float16}


@dataclass(slots=True)
class TrainConfig:
    """Hyperparameters for :class:`Trainer`."""

    out_dir: str = "runs/default"
    batch_size: int = 8
    val_batch_size: int = 16
    grad_accum_steps: int = 1
    max_epochs: int = 1
    max_steps_per_epoch: int | None = None  # cap for smoke tests / debugging
    lr: float = 3e-4
    min_lr_ratio: float = 0.1
    weight_decay: float = 0.1
    betas: tuple[float, float] = (0.9, 0.95)
    grad_clip_norm: float = 1.0
    warmup_steps: int = 20
    amp_dtype: str = "fp32"  # fp32 | bf16 | fp16
    eval_interval_steps: int | None = None  # None -> evaluate once per epoch
    log_every_steps: int = 10
    seed: int = 42
    num_workers: int = 0


class JsonlLogger:
    """Append-only JSON-lines experiment log."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, record: dict) -> None:
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **record}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=float) + "\n")


def _cpu_supports_bf16() -> bool:
    try:
        return bool(torch.tensor(1.0).bfloat16().isfinite())
    except Exception:
        return False


class Trainer:
    """Resumable trainer for autoregressive LMs over ``(x, y)`` datasets."""

    def __init__(
        self,
        model: torch.nn.Module,
        train_ds: Dataset,
        val_ds: Dataset | None = None,
        cfg: TrainConfig | None = None,
        device: str | None = None,
        extra_eval_fns: list[Callable[["Trainer"], dict]] | None = None,
    ) -> None:
        self.cfg = cfg or TrainConfig()
        if self.cfg.amp_dtype not in {"fp32", *_AMP_DTYPES}:
            raise ValueError(f"amp_dtype must be fp32/bf16/fp16, got {self.cfg.amp_dtype!r}")
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.device_type = str(self.device).split(":")[0]
        self.model = model.to(self.device)
        self.train_ds = train_ds
        self.val_ds = val_ds
        self.extra_eval_fns = list(extra_eval_fns or [])

        c = self.cfg
        self.out_dir = Path(c.out_dir)
        self.ckpt_dir = self.out_dir / "checkpoints"
        self.ckpt_dir.mkdir(parents=True, exist_ok=True)
        self.logger = JsonlLogger(self.out_dir / "train_log.jsonl")

        self._seed_everything(c.seed)
        pin = self.device_type == "cuda"
        self.train_loader = DataLoader(
            train_ds,
            batch_size=c.batch_size,
            shuffle=True,
            num_workers=c.num_workers,
            drop_last=True,
            pin_memory=pin,
        )
        self.val_loader = (
            DataLoader(val_ds, batch_size=c.val_batch_size, shuffle=False, pin_memory=pin)
            if val_ds is not None and len(val_ds) > 0
            else None
        )

        decay, no_decay = [], []
        for _, p in self.model.named_parameters():
            if p.requires_grad:
                (no_decay if p.ndim <= 1 else decay).append(p)
        self.optimizer = torch.optim.AdamW(
            [
                {"params": decay, "weight_decay": c.weight_decay},
                {"params": no_decay, "weight_decay": 0.0},
            ],
            lr=c.lr,
            betas=c.betas,
        )

        raw_steps = max(1, len(self.train_loader)) // max(1, c.grad_accum_steps)
        self.steps_per_epoch = min(raw_steps, c.max_steps_per_epoch or raw_steps)
        self.total_steps = self.steps_per_epoch * c.max_epochs
        warmup = min(c.warmup_steps, max(1, self.total_steps // 10))
        self._warmup = warmup

        def _lr_lambda(step: int) -> float:
            if step < warmup:
                return step / max(1, warmup)
            progress = (step - warmup) / max(1, self.total_steps - warmup)
            cosine = 0.5 * (1.0 + math.cos(math.pi * min(progress, 1.0)))
            return c.min_lr_ratio + (1.0 - c.min_lr_ratio) * cosine

        self.scheduler = torch.optim.lr_scheduler.LambdaLR(self.optimizer, _lr_lambda)

        requested = _AMP_DTYPES.get(c.amp_dtype)
        self.scaler = torch.amp.GradScaler(
            self.device_type, enabled=(c.amp_dtype == "fp16" and self.device_type == "cuda")
        )
        if c.amp_dtype != "fp32":
            if c.amp_dtype == "fp16" and self.device_type != "cuda":
                # FP16 Gradients are unstable on CPU; fall back to BF16 when usable.
                requested = torch.bfloat16 if _cpu_supports_bf16() else None
            self.amp_dtype = requested
            self._use_amp = requested is not None
        else:
            self.amp_dtype = None
            self._use_amp = False

        # Mutable run state (checkpointed).
        self.global_step = 0
        self.epoch = 0
        self.best_val_loss = float("inf")
        self.tokens_seen = 0

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _seed_everything(seed: int) -> None:
        random.seed(seed)
        np.random.seed(seed % (2**32))
        torch.manual_seed(seed)

    def _autocast(self):
        if not self._use_amp:
            return contextlib.nullcontext()
        return torch.autocast(self.device_type, dtype=self.amp_dtype)

    def current_lr(self) -> float:
        return self.optimizer.param_groups[0]["lr"]

    def _log(self, split: str, **fields: float | int) -> None:
        record = {"step": self.global_step, "epoch": self.epoch, "split": split, **fields}
        self.logger.log(record)
        pretty = " | ".join(
            f"{k}={v:.5g}" if isinstance(v, float) else f"{k}={v}" for k, v in fields.items()
        )
        print(f"[{split}] epoch {self.epoch} step {self.global_step} | {pretty}")

    # ------------------------------------------------------------- checkpoint

    def save_checkpoint(self, name: str = "latest") -> Path:
        """Persist full training state atomically; returns the checkpoint path."""
        path = self.ckpt_dir / f"{name}.pt"
        payload = {
            "model": self.model.state_dict(),
            "optimizer": self.optimizer.state_dict(),
            "scheduler": self.scheduler.state_dict(),
            "scaler": self.scaler.state_dict(),
            "global_step": self.global_step,
            "epoch": self.epoch,
            "best_val_loss": self.best_val_loss,
            "tokens_seen": self.tokens_seen,
            "train_config": asdict(self.cfg),
            "model_config": asdict(getattr(self.model, "cfg")),
            "rng": {
                "python": random.getstate(),
                "numpy": np.random.get_state(),
                "torch": torch.get_rng_state(),
            },
        }
        tmp = path.with_suffix(".pt.tmp")
        torch.save(payload, tmp)
        tmp.replace(path)  # atomic swap -> no corrupted half-written checkpoints
        return path

    def load_checkpoint(self, path: str | Path) -> None:
        """Restore model/optimizer/scheduler/RNG and continue where we left off."""
        payload = torch.load(path, map_location=self.device, weights_only=False)
        self.model.load_state_dict(payload["model"])
        self.optimizer.load_state_dict(payload["optimizer"])
        self.scheduler.load_state_dict(payload["scheduler"])
        self.scaler.load_state_dict(payload["scaler"])
        self.global_step = payload["global_step"]
        self.epoch = payload["epoch"]
        self.best_val_loss = payload["best_val_loss"]
        self.tokens_seen = payload["tokens_seen"]
        rng = payload["rng"]
        random.setstate(rng["python"])
        np.random.set_state(rng["numpy"])
        torch.set_rng_state(rng["torch"].cpu())
        print(f"resumed from {path}: step {self.global_step}, epoch {self.epoch}")

    # ------------------------------------------------------------- evaluation

    @torch.no_grad()
    def evaluate(self) -> dict[str, float]:
        """Validation loss/perplexity plus any registered extra eval hooks."""
        results: dict[str, float] = {}
        if self.val_loader is not None:
            results.update(lm_loss_and_perplexity(self.model, self.val_loader, self.device))
        for fn in self.extra_eval_fns:
            results.update(fn(self))
        if results:
            self._log("val", **results)
            if (v := results.get("loss")) is not None and v < self.best_val_loss:
                self.best_val_loss = v
                self.save_checkpoint("best")
        return results

    # ------------------------------------------------------------------ train

    def train(self) -> dict[str, float]:
        """Run the remaining epochs; returns the final validation metrics."""
        c = self.cfg
        accum = max(1, c.grad_accum_steps)
        final_metrics: dict[str, float] = {}
        for self.epoch in range(self.epoch, c.max_epochs):
            self.model.train()
            running, seen, t0 = 0.0, 0, time.perf_counter()
            micro = 0
            epoch_done = False
            self.optimizer.zero_grad(set_to_none=True)
            for x, y in self.train_loader:
                x = x.to(self.device, non_blocking=True)
                y = y.to(self.device, non_blocking=True)
                with self._autocast():
                    out = self.model(x, targets=y)
                    loss = out.loss / accum
                self.scaler.scale(loss).backward()
                running += out.loss.item()
                seen += y.numel()
                self.tokens_seen += y.numel()
                micro += 1
                if micro % accum != 0 and micro < len(self.train_loader):
                    continue
                self.scaler.unscale_(self.optimizer)
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), c.grad_clip_norm)
                self.scaler.step(self.optimizer)
                self.scaler.update()
                self.scheduler.step()
                self.optimizer.zero_grad(set_to_none=True)
                self.global_step += 1

                if self.global_step % c.log_every_steps == 0:
                    dt = time.perf_counter() - t0
                    self._log(
                        "train",
                        loss=running / micro,
                        lr=self.current_lr(),
                        tokens_per_sec=seen / dt if dt > 0 else 0.0,
                    )
                if c.eval_interval_steps and self.global_step % c.eval_interval_steps == 0:
                    self.evaluate()
                    self.model.train()
                if (
                    self.global_step >= self.steps_per_epoch * (self.epoch + 1)
                    or self.global_step >= self.total_steps
                ):
                    epoch_done = True
                if epoch_done:
                    break

            dt = max(time.perf_counter() - t0, 1e-9)
            final_metrics = self.evaluate()
            self.model.train()
            self._log("train", epoch_train_loss=running / max(1, micro), tokens_per_sec=seen / dt)
            self.save_checkpoint("latest")
            if self.global_step >= self.total_steps:
                break
        return final_metrics



