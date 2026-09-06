"""Rotary position embeddings (RoPE)."""

from __future__ import annotations

import torch


def build_rope_cache(seq_len: int, head_dim: int, theta: float = 10000.0) -> tuple[torch.Tensor, torch.Tensor]:
    """Return cos/sin caches of shape (seq_len, head_dim//2), FP32."""
    half = head_dim // 2
    inv_freq = 1.0 / (theta ** (torch.arange(0, half, dtype=torch.float32) / half))
    positions = torch.arange(seq_len, dtype=torch.float32)
    freqs = torch.outer(positions, inv_freq)
    return freqs.cos(), freqs.sin()


def apply_rope(x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
    """Apply rotary embedding to x of shape (B, T, H, D)."""
    x1, x2 = x.float().chunk(2, dim=-1)
    cos = cos.view(1, x.shape[1], 1, -1).to(device=x.device)
    sin = sin.view(1, x.shape[1], 1, -1).to(device=x.device)
    out1 = x1 * cos - x2 * sin
    out2 = x1 * sin + x2 * cos
    return torch.cat((out1, out2), dim=-1).type_as(x)