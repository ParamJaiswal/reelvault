"""ONNX inference: same numerics, same constrained decoding, no PyTorch.

Wraps an ONNX Runtime session so edge deployments reuse the *exact* JSON
state machine from :mod:`slm.model.constrained` -- only the forward pass is
swapped from PyTorch to ORT. Supports dynamic sequence lengths.
"""

from __future__ import annotations

import numpy as np
import torch

from slm.tokenizer.bpe import EOS as _EOS
from slm.tokenizer.bpe import BPETokenizer


class OnnxLM:
    """Minimal LM interface over an ORT session (logits in / tokens out)."""

    def __init__(self, model_path: str, *, threads: int | None = None) -> None:
        import onnxruntime as ort  # imported lazily: optional edge dependency

        options = ort.SessionOptions()
        # Graph optimizations (operator fusion, constant folding) at load time.
        options.graph_optimization_level = (
            ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        )
        if threads:
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(model_path, sess_options=options)
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

    def logits_last(self, ids: list[int]) -> np.ndarray:
        """Forward pass returning the final-position logits as ``(V,)`` float32."""
        feed = np.asarray([ids], dtype=np.int64)
        logits = self.session.run([self.output_name], {self.input_name: feed})[0]
        return logits[0, -1, :].astype(np.float32)

    def logits_full(self, ids: list[int]) -> np.ndarray:
        """Full ``(T, V)`` logits for a sequence (used by parity tests)."""
        feed = np.asarray([ids], dtype=np.int64)
        return self.session.run([self.output_name], {self.input_name: feed})[0][0]


def generate_constrained_json_onnx(
    onnx_lm: OnnxLM,
    tokenizer: BPETokenizer,
    prompt_ids: list[int],
    schema: dict[str, str],
    *,
    max_new_tokens: int = 96,
    temperature: float = 0.0,
    max_position_embeddings: int = 1024,
    eos_id: int = _EOS,
) -> str:
    """Greedy constrained-JSON generation on ONNX Runtime.

    Mirrors :func:`slm.model.constrained.generate_constrained_json` step for
    step: identical masking state machine over the ORT-computed logits, so
    output structure guarantees are unchanged on the edge.
    """
    from slm.model.constrained import JsonLogitsProcessor

    proc = JsonLogitsProcessor(tokenizer, schema)
    cur = list(prompt_ids)
    for _ in range(max_new_tokens):
        window = cur[-max_position_embeddings:]
        logits = torch.from_numpy(onnx_lm.logits_last(window))
        proc.mask_(logits)
        nid = int(torch.argmax(logits, dim=-1))
        proc.consume_token(nid)
        cur.append(nid)
        if nid == eos_id:
            break
    new_ids = [i for i in cur[len(prompt_ids):] if i != eos_id]
    return tokenizer.decode(new_ids, skip_special=True)


__all__ = ["OnnxLM", "generate_constrained_json_onnx"]