"""Model configuration and parameter-count utilities."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(slots=True)
class ModelConfig:
    """Configuration for the decoder-only transformer."""

    vocab_size: int = 8192
    hidden_size: int = 192
    num_layers: int = 5
    num_heads: int = 6
    num_kv_heads: int | None = None  # None -> same as num_heads (full MHA)
    ffn_mult: float = 8.0
    max_position_embeddings: int = 1024
    rope_theta: float = 10000.0
    tie_embeddings: bool = True
    dropout: float = 0.0
    pad_token_id: int = 0

    def __post_init__(self) -> None:
        if self.num_kv_heads is None:
            self.num_kv_heads = self.num_heads
        if self.num_kv_heads > self.num_heads:
            raise ValueError("num_kv_heads must be <= num_heads")
        if self.num_heads % self.num_kv_heads != 0:
            raise ValueError("num_heads must be divisible by num_kv_heads")
        if self.hidden_size % self.num_heads != 0:
            raise ValueError("hidden_size must be divisible by num_heads")

    @property
    def ffn_size(self) -> int:
        """SwiGLU hidden dimension, rounded to a multiple of 8."""
        return int(round(self.hidden_size * self.ffn_mult / 8)) * 8

    def head_dim(self) -> int:
        return self.hidden_size // self.num_heads


def estimate_num_params(cfg: ModelConfig, *, count_embedding_only: bool = False) -> int:
    """Deterministic parameter-count estimator (matches the built model)."""
    h, v = cfg.hidden_size, cfg.vocab_size
    n_emb = v * h
    if count_embedding_only:
        return n_emb
    head_dim = cfg.head_dim()
    q_proj = h * h
    kv_proj = 2 * h * (cfg.num_kv_heads * head_dim)
    o_proj = h * h
    attn = q_proj + kv_proj + o_proj
    ffn = 3 * h * cfg.ffn_size  # SwiGLU gate + up + down
    per_layer = attn + ffn + 2 * h  # 2 RMSNorms per block (attention + FFN)
    final_norm = h
    lm_head = 0 if cfg.tie_embeddings else v * h
    return n_emb + cfg.num_layers * per_layer + final_norm + lm_head


def suggest_configs(
    targets: dict[str, int],
    vocab_size: int,
    *,
    max_seq_len: int = 1024,
    prefer_gqa_at: int | None = 50_000_000,
) -> dict[str, ModelConfig]:
    """Systematically search layer/hidden/head combos nearest each parameter target."""
    results: dict[str, ModelConfig] = {}
    for name, target in sorted(targets.items(), key=lambda kv: kv[1]):
        best: tuple[int, ModelConfig] | None = None
        for layers in range(4, 25):
            for hidden in range(128, 1537, 64):
                for num_heads in (4, 6, 8, 12, 16):
                    if hidden % num_heads != 0:
                        continue
                    kv = num_heads // 2 if (
                        prefer_gqa_at is not None and target >= prefer_gqa_at
                    ) else num_heads
                    cfg = ModelConfig(
                        vocab_size=vocab_size,
                        hidden_size=hidden,
                        num_layers=layers,
                        num_heads=num_heads,
                        num_kv_heads=max(1, kv),
                        max_position_embeddings=max_seq_len,
                    )
                    n = estimate_num_params(cfg)
                    err = abs(n - target)
                    if best is None or err < best[0]:
                        best = (err, cfg)
        assert best is not None
        results[name] = replace(best[1])
    return results


# Baseline-class configuration. Historical name says 3.6M but the exact count
# at 8192 vocab is 6,735,936 parameters (see estimate_num_params / tests).
BASELINE_3_6M = ModelConfig(
    vocab_size=8192,
    hidden_size=192,
    num_layers=5,
    num_heads=6,
    num_kv_heads=6,
    ffn_mult=8.0,
)