"""Decoder-only transformer: RMSNorm, RoPE, SwiGLU, GQA, tied embeddings."""

from __future__ import annotations

import math
from dataclasses import dataclass

import torch
from torch import nn
from torch.nn import functional as F

from slm.model.config import ModelConfig
from slm.model.rope import apply_rope, build_rope_cache


class RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-6) -> None:
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        dtype = x.dtype
        x = x.float()
        norm = torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + self.eps)
        return (x * norm).to(dtype) * self.weight


class SwiGLU(nn.Module):
    def __init__(self, hidden: int, ffn: int) -> None:
        super().__init__()
        self.gate_proj = nn.Linear(hidden, ffn, bias=False)
        self.up_proj = nn.Linear(hidden, ffn, bias=False)
        self.down_proj = nn.Linear(ffn, hidden, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.down_proj(F.silu(self.gate_proj(x)) * self.up_proj(x))


class Attention(nn.Module):
    def __init__(self, cfg: ModelConfig) -> None:
        super().__init__()
        self.num_heads = cfg.num_heads
        self.num_kv_heads = cfg.num_kv_heads
        self.head_dim = cfg.head_dim()
        self.q_proj = nn.Linear(cfg.hidden_size, self.num_heads * self.head_dim, bias=False)
        self.k_proj = nn.Linear(cfg.hidden_size, self.num_kv_heads * self.head_dim, bias=False)
        self.v_proj = nn.Linear(cfg.hidden_size, self.num_kv_heads * self.head_dim, bias=False)
        self.o_proj = nn.Linear(self.num_heads * self.head_dim, cfg.hidden_size, bias=False)

    def forward(
        self,
        x: torch.Tensor,
        cos: torch.Tensor,
        sin: torch.Tensor,
        attn_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        bsz, seq, _ = x.shape
        q = self.q_proj(x).view(bsz, seq, self.num_heads, self.head_dim)
        k = self.k_proj(x).view(bsz, seq, self.num_kv_heads, self.head_dim)
        v = self.v_proj(x).view(bsz, seq, self.num_kv_heads, self.head_dim)
        q, k, v = apply_rope(q, cos, sin), apply_rope(k, cos, sin), v.float()

        group = self.num_heads // self.num_kv_heads
        if group > 1:
            k = k.repeat_interleave(group, dim=2)
            v = v.repeat_interleave(group, dim=2)
        q = q.transpose(1, 2)
        k = k.transpose(1, 2)
        v = v.transpose(1, 2)
        out = F.scaled_dot_product_attention(q, k, v, attn_mask=attn_mask)
        out = out.transpose(1, 2).reshape(bsz, seq, -1)
        return self.o_proj(out)


class Block(nn.Module):
    def __init__(self, cfg: ModelConfig) -> None:
        super().__init__()
        self.attn_norm = RMSNorm(cfg.hidden_size)
        self.attn = Attention(cfg)
        self.ffn_norm = RMSNorm(cfg.hidden_size)
        self.ffn = SwiGLU(cfg.hidden_size, cfg.ffn_size)

    def forward(
        self,
        x: torch.Tensor,
        cos: torch.Tensor,
        sin: torch.Tensor,
        attn_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        x = x + self.attn(self.attn_norm(x), cos, sin, attn_mask)
        return x + self.ffn(self.ffn_norm(x))


@dataclass(slots=True)
class LMOutput:
    logits: torch.Tensor
    loss: torch.Tensor | None = None


class TransformerLM(nn.Module):
    """Autoregressive LM with cache-less greedy/sampling generation."""

    def __init__(self, cfg: ModelConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self.embed = nn.Embedding(cfg.vocab_size, cfg.hidden_size, padding_idx=cfg.pad_token_id)
        self.blocks = nn.ModuleList(Block(cfg) for _ in range(cfg.num_layers))
        self.final_norm = RMSNorm(cfg.hidden_size)
        if cfg.tie_embeddings:
            self.lm_head: nn.Linear | None = None
            head_weight = self.embed.weight
        else:
            self.lm_head = nn.Linear(cfg.hidden_size, cfg.vocab_size, bias=False)
            head_weight = self.lm_head.weight
        self._head_weight = head_weight
        cos, sin = build_rope_cache(cfg.max_position_embeddings, cfg.head_dim(), cfg.rope_theta)
        self.register_buffer("rope_cos", cos, persistent=False)
        self.register_buffer("rope_sin", sin, persistent=False)
        self.apply(self._init_weights)

    def _init_weights(self, module: nn.Module) -> None:
        if isinstance(module, nn.Linear):
            nn.init.normal_(module.weight, std=0.02)
            if module.bias is not None:
                nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            nn.init.normal_(module.weight, std=0.02)
            if module.padding_idx is not None:
                with torch.no_grad():
                    module.weight[module.padding_idx].fill_(0)

    def _attn_mask(self, idx: torch.Tensor) -> torch.Tensor:
        bsz, seq = idx.shape
        neg_inf = float("-inf")
        pad_neg = torch.finfo(torch.get_default_dtype()).min
        causal = torch.triu(torch.full((seq, seq), neg_inf), diagonal=1)
        # Use a large *finite* negative so 0 * pad_neg stays 0.0 (0 * -inf would be NaN).
        key_pad = (idx == self.cfg.pad_token_id).to(causal.dtype) * pad_neg
        return causal.view(1, 1, seq, seq).to(idx.device) + key_pad.view(bsz, 1, 1, seq).to(idx.device)

    def hidden_states(self, idx: torch.Tensor) -> torch.Tensor:
        seq = idx.shape[1]
        mask = self._attn_mask(idx)
        x = self.embed(idx)
        for block in self.blocks:
            x = block(x, self.rope_cos[:seq].to(x.device), self.rope_sin[:seq].to(x.device), mask)
        return self.final_norm(x)

    def forward(
        self,
        idx: torch.Tensor,
        targets: torch.Tensor | None = None,
    ) -> LMOutput:
        if idx.shape[1] > self.cfg.max_position_embeddings:
            raise ValueError(
                f"sequence length {idx.shape[1]} exceeds max_position_embeddings "
                f"{self.cfg.max_position_embeddings}"
            )
        x = self.hidden_states(idx)
        logits = F.linear(x, self._head_weight) if self.lm_head is None else self.lm_head(x)
        loss = None
        if targets is not None:
            loss = F.cross_entropy(
                logits.reshape(-1, logits.size(-1)).float(),
                targets.reshape(-1),
                ignore_index=-100,
            )
        return LMOutput(logits=logits, loss=loss)

    @torch.no_grad()
    def generate(
        self,
        idx: torch.Tensor,
        max_new_tokens: int,
        *,
        temperature: float = 0.0,
        top_k: int | None = None,
        eos_id: int | None = None,
    ) -> torch.Tensor:
        was_training = self.training
        self.eval()
        cur = idx
        for _ in range(max_new_tokens):
            out = self(cur[:, -self.cfg.max_position_embeddings :])
            logits = out.logits[:, -1, :]
            if temperature <= 0:
                next_id = torch.argmax(logits, dim=-1, keepdim=True)
            else:
                logits = logits / temperature
                if top_k is not None:
                    kth = torch.topk(logits, min(top_k, logits.size(-1)), dim=-1).values[..., -1:]
                    logits = logits.masked_fill(logits < kth, float("-inf"))
                next_id = torch.multinomial(F.softmax(logits, dim=-1), 1)
            cur = torch.cat((cur, next_id), dim=1)
            if eos_id is not None and bool((next_id == eos_id).all()):
                break
        if was_training:
            self.train()
        return cur

    def num_params(self, *, non_embedding: bool = False) -> int:
        n = sum(p.numel() for p in self.parameters())
        if non_embedding:
            n -= self.cfg.vocab_size * self.cfg.hidden_size
        return n


class ClassifierHead(nn.Module):
    """Mean-pooled classification head over the backbone."""

    def __init__(
        self, backbone: TransformerLM, num_classes: int, *, freeze_backbone: bool = False
    ) -> None:
        super().__init__()
        self.backbone = backbone
        if freeze_backbone:
            for p in self.backbone.parameters():
                p.requires_grad_(False)
        self.dropout = nn.Dropout(0.1)
        self.score = nn.Linear(backbone.cfg.hidden_size, num_classes)

    def forward(self, idx: torch.Tensor, attention_mask: torch.Tensor | None = None) -> torch.Tensor:
        h = self.backbone.hidden_states(idx)
        if attention_mask is not None:
            m = attention_mask.unsqueeze(-1).to(h.dtype)
            pooled = (h * m).sum(dim=1) / m.sum(dim=1).clamp(min=1e-6)
        else:
            pooled = h.mean(dim=1)
        return self.score(self.dropout(pooled))