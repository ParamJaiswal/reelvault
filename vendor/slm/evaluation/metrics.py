"""Permanent evaluation framework: LM perplexity, classification metrics, JSON validity.

Pure functions over tensors/lists so they stay decoupled from any particular model,
dataset, or trainer. The Trainer consumes these through its eval hooks.
"""

from __future__ import annotations

import json
from typing import Iterable, Sequence

import torch

# Cross-entropy values above this are treated as divergent rather than exponentiated.
_MAX_LOSS_FOR_EXP = 20.0


def perplexity_from_loss(mean_loss: float) -> float:
    """Perplexity of a mean cross-entropy loss, clamped to avoid overflow."""
    return float(torch.exp(torch.tensor(min(float(mean_loss), _MAX_LOSS_FOR_EXP))))


@torch.no_grad()
def lm_loss_and_perplexity(model, loader, device: str | torch.device) -> dict[str, float]:
    """Mean token loss + perplexity of an LM over a dataloader."""
    model.eval()
    total_loss, total_tokens = 0.0, 0
    for x, y in loader:
        x, y = x.to(device), y.to(device)
        out = model(x, targets=y)
        n = y.numel()
        total_loss += out.loss.item() * n
        total_tokens += n
    model.train()
    if total_tokens == 0:
        raise ValueError("empty evaluation loader")
    mean_loss = total_loss / total_tokens
    return {"loss": mean_loss, "perplexity": perplexity_from_loss(mean_loss)}


def classification_metrics(y_true: Sequence[int], y_pred: Sequence[int]) -> dict[str, float]:
    """Accuracy plus macro precision/recall/F1 (no sklearn dependency)."""
    if not y_true or len(y_true) != len(y_pred):
        raise ValueError("y_true/y_pred must be non-empty and equal length")
    labels = sorted(set(y_true) | set(y_pred))
    correct = sum(t == p for t, p in zip(y_true, y_pred))
    precisions, recalls, f1s = [], [], []
    for label in labels:
        tp = sum(p == label and t == label for t, p in zip(y_true, y_pred))
        fp = sum(p == label and t != label for t, p in zip(y_true, y_pred))
        fn = sum(t == label and p != label for t, p in zip(y_true, y_pred))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        precisions.append(precision)
        recalls.append(recall)
        f1s.append(f1)
    return {
        "accuracy": correct / len(y_true),
        "precision_macro": sum(precisions) / len(labels),
        "recall_macro": sum(recalls) / len(labels),
        "f1_macro": sum(f1s) / len(labels),
    }


@torch.no_grad()
def evaluate_classifier(
    head,
    loader,
    device: str | torch.device,
    ignore_index: int = -100,
) -> dict[str, float]:
    """Run a ClassifierHead over a loader yielding (idx, attention_mask|None, labels)."""
    head.eval()
    y_true: list[int] = []
    y_pred: list[int] = []
    for batch in loader:
        idx, mask, labels = batch[0], batch[1] if len(batch) == 3 else None, batch[-1]
        idx, labels = idx.to(device), labels.to(device)
        logits = head(idx, attention_mask=mask.to(device) if mask is not None else None)
        keep = labels != ignore_index
        if keep.any():
            y_pred.extend(logits[keep].argmax(dim=-1).tolist())
            y_true.extend(labels[keep].tolist())
    head.train()
    return classification_metrics(y_true, y_pred)


def parse_json_candidates(outputs: Iterable[str]) -> list[object | None]:
    """Parse each output as JSON; unparsable entries become ``None``."""
    parsed: list[object | None] = []
    for text in outputs:
        try:
            parsed.append(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            parsed.append(None)
    return parsed


def json_validity(outputs: Sequence[str]) -> float:
    """Fraction of outputs that parse as syntactically valid JSON."""
    if not outputs:
        raise ValueError("no outputs to score")
    ok = sum(p is not None for p in parse_json_candidates(outputs))
    return ok / len(outputs)


def schema_validity(outputs: Sequence[str], required_keys: Sequence[str]) -> float:
    """Fraction of outputs that parse AND contain every required top-level key."""
    if not outputs:
        raise ValueError("no outputs to score")
    required = set(required_keys)
    valid = sum(
        isinstance(p, dict) and required.issubset(p)
        for p in parse_json_candidates(outputs)
    )
    return valid / len(outputs)
