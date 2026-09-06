"""Parameter-efficient fine-tuning: LoRA adapters over the frozen base model.

Implements Low-Rank Adaptation from scratch (no external PEFT dependency):

- ``LoRALinear`` wraps an existing ``nn.Linear`` with a low-rank residual
  ``delta(x) = (x @ A^T) @ B^T * (alpha / r)``. ``B`` starts at zero so the
  wrapped model is exactly equivalent to the base model at step 0.
- ``inject_lora`` dynamically replaces matching ``nn.Linear`` attributes
  (attention Q/K/V/O projections and the SwiGLU gate/up/down layers),
  freezing every base weight. The base transformer is never rewritten --
  wrapping only.
- ``merge_and_unload`` bakes trained deltas back into the base FP32 weights
  and restores plain ``nn.Linear`` modules, so inference cost and state-dict
  layout are identical to the original architecture (zero-added-latency).

The existing :class:`~slm.training.trainer.Trainer` already builds its
optimizer exclusively from parameters with ``requires_grad=True``, so it
trains only adapter matrices without modification.
"""

from __future__ import annotations

import math

import torch
from torch import nn

DEFAULT_TARGETS = (
    "q_proj", "k_proj", "v_proj", "o_proj",       # attention projections
    "gate_proj", "up_proj", "down_proj",          # SwiGLU FFN
)


class LoRALinear(nn.Module):
    """Wraps a frozen ``nn.Linear`` with trainable low-rank A/B matrices."""

    def __init__(
        self,
        base: nn.Linear,
        r: int = 8,
        alpha: float = 32.0,
        dropout: float = 0.0,
    ) -> None:
        super().__init__()
        if r < 1:
            raise ValueError(f"LoRA rank must be >= 1, got {r}")
        in_features = base.in_features
        out_features = base.out_features
        self.base = base
        self.r, self.alpha = r, float(alpha)
        self.scaling = self.alpha / r
        self.lora_dropout = nn.Dropout(dropout) if dropout > 0 else nn.Identity()
        std = 1.0 / math.sqrt(in_features)
        self.lora_A = nn.Parameter(torch.randn(r, in_features) * std)
        self.lora_B = nn.Parameter(torch.zeros(out_features, r))  # zero-init: no-op at t=0
        # Freeze the wrapped base layer immediately.
        for p in self.base.parameters():
            p.requires_grad_(False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        base_out = self.base(x)
        delta = (self.lora_dropout(x) @ self.lora_A.transpose(0, 1)) @ self.lora_B.transpose(0, 1)
        return base_out + delta * self.scaling

    def merged_weight(self) -> torch.Tensor:
        """Effective weight after folding the low-rank update into the base."""
        return self.base.weight.data + self.scaling * (self.lora_B @ self.lora_A)

    def extra_repr(self) -> str:  # pragma: no cover - repr cosmetics
        return f"r={self.r}, alpha={self.alpha}, scaling={self.scaling:g}"


def _get_parent(model: nn.Module, qualified_name: str) -> tuple[nn.Module, str]:
    parts = qualified_name.split(".")
    parent = model
    for part in parts[:-1]:
        parent = getattr(parent, part)
    return parent, parts[-1]


@torch.no_grad()
def inject_lora(
    model: nn.Module,
    *,
    r: int = 8,
    alpha: float = 32.0,
    dropout: float = 0.0,
    target_names: tuple[str, ...] = DEFAULT_TARGETS,
    freeze_base: bool = True,
) -> dict:
    """Wrap matching ``nn.Linear`` layers with :class:`LoRALinear` in place.

    Returns an info dict with injection statistics. If ``freeze_base`` is set,
    every non-adapter parameter is marked ``requires_grad=False``.
    """
    replaced: list[str] = []
    for name, module in list(model.named_modules()):
        parent, leaf = _get_parent(model, name)
        child = getattr(parent, leaf, None)
        if child is None or not isinstance(child, nn.Linear):
            continue
        if leaf not in target_names or isinstance(child, LoRALinear):
            continue
        setattr(parent, leaf, LoRALinear(child, r=r, alpha=alpha, dropout=dropout))
        replaced.append(name)

    if not replaced:
        raise ValueError(
            f"no target layers found among {target_names}; "
            "model may already be injected or uses different attribute names"
        )
    if freeze_base:
        mark_only_lora_trainable(model)

    total, trainable = param_counts(model)
    return {
        "replaced": replaced,
        "num_adapters": len(replaced),
        "rank": r,
        "alpha": alpha,
        "total_params": total,
        "trainable_params": trainable,
        "trainable_pct": round(100.0 * trainable / max(1, total), 4),
    }


def mark_only_lora_trainable(model: nn.Module) -> None:
    """Freeze everything except LoRA adapter matrices."""
    for name, p in model.named_parameters():
        p.requires_grad_("lora_" in name)


def lora_parameters(model: nn.Module) -> list[nn.Parameter]:
    return [p for n, p in model.named_parameters() if "lora_" in n]


def param_counts(model: nn.Module) -> tuple[int, int]:
    total = sum(p.numel() for p in model.parameters())
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    return total, trainable


@torch.no_grad()
def merge_and_unload(model: nn.Module) -> nn.Module:
    """Fold every LoRA delta into the base weights; restore plain ``nn.Linear``.

    After this call the module tree and ``state_dict`` keys are exactly those
    of the original architecture, so inference latency and checkpoint format
    carry zero LoRA overhead.
    """
    merges = 0
    for name, module in list(model.named_modules()):
        if not isinstance(module, LoRALinear):
            continue
        parent, leaf = _get_parent(model, name)
        base = module.base
        merged_linear = nn.Linear(
            base.in_features, base.out_features,
            bias=base.bias is not None,
            dtype=base.weight.dtype, device=base.weight.device,
        )
        merged_linear.weight = nn.Parameter(module.merged_weight().clone())
        if base.bias is not None:
            merged_linear.bias = nn.Parameter(base.bias.data.clone())
        setattr(parent, leaf, merged_linear)
        merges += 1

    if merges == 0:
        raise ValueError("no LoRALinear layers found to merge")
    # Unfreeze everything: the merged model is a normal model again.
    for p in model.parameters():
        p.requires_grad_(True)
    return model


def lora_state_dict(model: nn.Module) -> dict[str, torch.Tensor]:
    """Adapter-only state dict (for lightweight adapter checkpoints)."""
    return {
        name: p.detach().cpu()
        for name, p in model.state_dict().items()
        if "lora_" in name
    }