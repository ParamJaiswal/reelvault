"""Logit-level constrained decoding: force schema-valid JSON output.

Small models hallucinate syntax; instead of prompt engineering we enforce a
JSON state machine directly on the logits before argmax/sampling. At every
generation step, tokens whose decoded text cannot legally continue the JSON
document are masked to ``-inf``. Required fields are enforced in fixed order,
so any completed generation parses and contains every required key.

Public API:
- ``JsonLogitsProcessor(tokenizer, schema)`` -- ``schema`` maps field name ->
  value type among ``"str" | "num" | "bool" | "null"``. Usable standalone via
  ``mask_()`` / ``consume_token()``, or as a transformers-style callable.
- ``generate_constrained_json(model, tokenizer, prompt_ids, schema, ...)``.
"""

from __future__ import annotations

import copy

import torch

from slm.tokenizer.bpe import EOS

_VALID_TYPES = {"str", "num", "bool", "null"}
_HEX = set("0123456789abcdefABCDEF")
_WS = " \t\r\n"
_STR_CAP = 64   # max chars per string value -> guarantees termination
_NUM_CAP = 24   # max chars per number value


class _Machine:
    """State machine accepting only strings on a path to schema-valid JSON."""

    __slots__ = (
        "fields", "phase", "field_idx", "lit_target",
        "lit_pos", "has_digit", "frac_digits", "hex_left", "value_chars",
    )

    def __init__(self, schema: dict[str, str]) -> None:
        bad = {k: v for k, v in schema.items() if str(v) not in _VALID_TYPES}
        if bad:
            raise ValueError(f"unsupported value types {bad}; use {_VALID_TYPES}")
        self.fields: list[tuple[str, str]] = [(str(k), str(v)) for k, v in schema.items()]
        self.phase = "open"
        self.field_idx = 0
        self.lit_target = ""
        self.lit_pos = 0
        self.has_digit = False
        self.frac_digits = 0
        self.hex_left = 0
        self.value_chars = 0

    def clone(self) -> "_Machine":
        return copy.copy(self)

    def feed(self, text: str) -> bool:
        """Consume ``text`` char by char; False means it breaks the grammar."""
        for ch in text:
            if not self._step(ch):
                return False
        return True

    def _step(self, ch: str) -> bool:
        ph = self.phase
        if ph == "done":
            return ch in _WS
        if ph == "open":
            if ch in _WS:
                return True
            if ch == "{":
                self.phase, self.field_idx = "pre_key", 0
                return True
            return False
        if ph == "pre_key":
            if ch in _WS:
                return True
            if ch == "}" and self.field_idx >= len(self.fields):
                self.phase = "done"
                return True
            if ch == '"':
                self.lit_pos = 0
                self.phase = "in_key"
                return True
            return False
        if ph == "in_key":
            target = self.fields[self.field_idx][0]
            if ch != target[self.lit_pos]:
                return False
            self.lit_pos += 1
            if self.lit_pos == len(target):
                self.phase = "post_key"  # still need the closing '"'
            return True
        if ph == "post_key":
            if ch == '"':
                self.phase = "pre_colon"
                return True
            return False
        if ph == "pre_colon":
            if ch in _WS:
                return True
            if ch == ":":
                self.phase = "pre_value"
                return True
            return False
        if ph == "pre_value":
            return self._open_value(ch)
        if ph == "in_str":
            if ord(ch) < 0x20:
                return False  # raw control chars are illegal inside JSON strings
            # Length cap: once full, only the closing quote may follow
            # (escape sequences would exceed it further).
            if ch == '"':
                self.phase = "after"
                return True
            if ch == "\\":
                if self.value_chars + 2 > _STR_CAP:
                    return False
                self.phase = "escape"
                self.value_chars += 1
                return True
            if self.value_chars >= _STR_CAP:
                return False
            self.value_chars += 1
            return True
        if ph == "escape":
            if ch in '"\\/bfnrt':
                self.phase = "in_str"
                return True
            if ch == "u":
                self.hex_left, self.phase = 4, "hex4"
                return True
            return False
        if ph == "hex4":
            if ch not in _HEX:
                return False
            self.hex_left -= 1
            if self.hex_left == 0:
                self.phase = "in_str"
            return True
        return self._step_late(ph, ch)

    def _step_late(self, ph: str, ch: str) -> bool:
        if ph in ("num_int", "num_frac"):
            if ph == "num_int" and ch.isdigit():
                if self.value_chars >= _NUM_CAP:
                    return False
                self.has_digit = True
                self.value_chars += 1
                return True
            if ph == "num_frac" and ch.isdigit():
                if self.value_chars >= _NUM_CAP:
                    return False
                self.frac_digits += 1
                self.value_chars += 1
                return True
            if ph == "num_int" and ch == ".":
                if self.value_chars + 1 >= _NUM_CAP:
                    return False  # reserve a slot: fraction needs >= 1 digit
                self.phase, self.frac_digits = "num_frac", 0
                return True
            if ch == ",":
                ok = self.has_digit and not (ph == "num_frac" and self.frac_digits == 0)
                return self._next_field(ok)
            if ch == "}":
                return self._close_object(self.has_digit)
            return False
        if ph == "in_lit":
            if ch != self.lit_target[self.lit_pos]:
                return False
            self.lit_pos += 1
            if self.lit_pos == len(self.lit_target):
                self.phase = "after"
            return True
        if ph == "after":
            if ch in _WS:
                return True
            if ch == ",":
                return self._next_field(True)
            if ch == "}":
                return self._close_object(True)
            return False
        return False

    def _open_value(self, ch: str) -> bool:
        vtype = self.fields[self.field_idx][1]
        if ch in _WS:
            return True
        if vtype == "str":
            if ch != '"':
                return False
            self.phase = "in_str"
            self.value_chars = 0
            return True
        if vtype == "num":
            self.has_digit, self.frac_digits = False, 0
            self.value_chars = 0
            self.phase = "num_int"
            if ch == "-":
                return True
            if not ch.isdigit():
                return False  # e.g. '.5' has no integer part
            return self._step(ch)
        starts = {"t": "true", "f": "false", "n": "null"}
        if ch not in starts:
            return False
        self.lit_target, self.lit_pos = starts[ch], 1
        self.phase = "in_lit"
        if self.lit_pos == len(self.lit_target):
            self.phase = "after"
        return True

    def _next_field(self, needs: bool) -> bool:
        """',' -> advance to next required key (never skip one)."""
        if not needs or self.field_idx >= len(self.fields) - 1:
            return False
        self.field_idx += 1
        self.phase = "pre_key"
        return True

    def _close_object(self, ok: bool) -> bool:
        """'}' -> only legal once the LAST required field is complete."""
        if not ok or self.field_idx != len(self.fields) - 1:
            return False
        self.phase = "done"
        return True


class JsonLogitsProcessor:
    """Masks logits so only schema-valid JSON continuations are possible."""

    def __init__(self, tokenizer, schema: dict[str, str]) -> None:
        self.tokenizer = tokenizer
        self.n_special = len(tokenizer.special_tokens)
        base = tokenizer.tok.get_vocab_size(with_added_tokens=True)
        texts: dict[int, str] = {}
        for raw in range(base):
            s = tokenizer.tok.decode([raw])
            if s:
                texts[raw + self.n_special] = s
        self.token_text = texts
        self.eos_id = EOS
        self.schema = {str(k): str(v) for k, v in schema.items()}
        self.machine = _Machine(self.schema)
        # State for transformers-style batched __call__ usage.
        self._row_machines: list[_Machine] | None = None
        self._row_seen: list[int] = []

    def reset(self) -> None:
        self.machine = _Machine(self.schema)
        self._row_machines = None
        self._row_seen = []

    def _allowed_ids(self, machine: _Machine) -> list[int]:
        keep: list[int] = []
        for gid, text in self.token_text.items():
            if not text.strip():
                continue  # pure-whitespace tokens never progress the document
            m = machine.clone()
            try:
                ok = m.feed(text)
            except Exception:
                ok = False
            if ok:
                keep.append(gid)
        return keep

    def _apply_mask(self, logits: torch.Tensor, machine: _Machine) -> torch.Tensor:
        """Mask a single ``(V,)`` logits row in place."""
        mask = torch.full_like(logits, float("-inf"))
        if machine.phase == "done":
            mask[self.eos_id] = 0.0  # document complete: only EOS may follow
        else:
            keep = self._allowed_ids(machine)
            if keep:
                mask[torch.tensor(keep, dtype=torch.long)] = 0.0
        logits += mask
        return logits

    def mask_(self, logits: torch.Tensor) -> torch.Tensor:
        """In-place masking of ``(V,)`` logits given the current state."""
        return self._apply_mask(logits, self.machine)

    def consume_token(self, token_id: int) -> None:
        """Advance the internal state after a token was actually generated."""
        text = self.token_text.get(int(token_id))
        if text is not None:
            self.machine.feed(text)

    def __call__(self, input_ids: torch.Tensor, scores: torch.Tensor) -> torch.Tensor:
        """transformers.LogitsProcessor-compatible hook over ``(B, T)``/``(B, V)``."""
        bsz = scores.size(0)
        if self._row_machines is None or len(self._row_machines) != bsz:
            self._row_machines = [_Machine(self.schema) for _ in range(bsz)]
            self._row_seen = [0] * bsz
        for r in range(bsz):
            row_machine = self._row_machines[r]
            for tid in input_ids[r, self._row_seen[r]:].tolist():
                text = self.token_text.get(tid)
                if text is not None:
                    row_machine.feed(text)
            self._row_seen[r] = int(input_ids.shape[1])
            self._apply_mask(scores[r], row_machine)
        return scores


@torch.no_grad()
def generate_constrained_json(
    model,
    tokenizer,
    prompt_ids: list[int],
    schema: dict[str, str],
    *,
    max_new_tokens: int = 96,
    temperature: float = 0.0,
    top_k: int | None = None,
) -> str:
    """Greedy/sampling generation hard-guaranteed to be schema-valid JSON."""
    import torch as _torch

    proc = JsonLogitsProcessor(tokenizer, schema)
    device = next(model.parameters()).device
    cur = _torch.tensor([prompt_ids], dtype=torch.long, device=device)
    prompt_len = cur.shape[1]
    max_pos = model.cfg.max_position_embeddings
    for _ in range(max_new_tokens):
        window = cur[:, -max_pos:]
        logits = model(window).logits[:, -1, :].float()
        proc.mask_(logits[0])
        if temperature <= 0:
            nid = _torch.argmax(logits, dim=-1)
        else:
            scaled = logits / temperature
            if top_k is not None:
                kth = _torch.topk(scaled, min(top_k, scaled.numel())).values[-1]
                scaled = _torch.where(scaled < kth, _torch.full_like(scaled, float("-inf")), scaled)
            probs = _torch.softmax(scaled, dim=-1)
            nid = _torch.multinomial(probs, 1).squeeze(-1)
        tid = int(nid)
        proc.consume_token(tid)
        cur = _torch.cat((cur, nid.view(1, 1)), dim=1)
        if tid == proc.eos_id:
            break
    new_ids = cur[0, prompt_len:].tolist()
    return tokenizer.decode([i for i in new_ids if i != proc.eos_id], skip_special=True)

