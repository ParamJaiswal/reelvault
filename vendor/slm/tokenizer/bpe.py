"""Tokenizer engineering: ByteLevel BPE trained from business-domain corpora.

Special tokens guarantee perfect round-trips of structured payloads, and digits
are preserved verbatim so invoice numbers / IDs are never corrupted.
"""

from __future__ import annotations

import json
from pathlib import Path

from tokenizers import ByteLevelBPETokenizer, Tokenizer

SPECIAL_TOKENS = ["<pad>", "<bos>", "<eos>", "<user>", "<assistant>", "<json>"]
PAD, BOS, EOS = 0, 1, 2


def train_bpe(
    corpus_files: list[str | Path],
    vocab_size: int,
    out_dir: str | Path,
    *,
    min_freq: int = 2,
) -> "BPETokenizer":
    """Train a byte-level BPE tokenizer and persist it with metadata."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tok = ByteLevelBPETokenizer(lowercase=False)
    tok.train(
        files=[str(p) for p in corpus_files],
        vocab_size=vocab_size - len(SPECIAL_TOKENS),
        min_frequency=min_freq,
        special_tokens=[],
    )
    # Save in Tokenizer JSON format so BPETokenizer.load() can read it back.
    tok.save(str(Path(out) / "tokenizer.json"))
    meta = {"special_tokens": SPECIAL_TOKENS, "requested_vocab": vocab_size}
    (out / "tokenizer_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return BPETokenizer.load(out)


class BPETokenizer:
    """Thin wrapper exposing ids/strings plus special-token bookkeeping."""

    def __init__(self, tok: Tokenizer, special_tokens: list[str]) -> None:
        self.tok = tok
        self.special_tokens = special_tokens
        # Specials occupy ids [0, len(special)) so PAD/BOS/EOS match the module
        # constants (0/1/2) and cannot collide with the +len(special)-shifted
        # regular vocabulary used by encode()/decode().
        self.special_ids = {name: i for i, name in enumerate(special_tokens)}
        self._id_to_special = {v: k for k, v in self.special_ids.items()}

    @classmethod
    def load(cls, path: str | Path) -> "BPETokenizer":
        path = Path(path)
        tok = Tokenizer.from_file(str(path / "tokenizer.json"))
        meta = json.loads((path / "tokenizer_meta.json").read_text(encoding="utf-8"))
        return cls(tok, list(meta["special_tokens"]))

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        self.tok.save(str(path / "tokenizer.json"))
        (path / "tokenizer_meta.json").write_text(
            json.dumps({"special_tokens": self.special_tokens}), encoding="utf-8"
        )

    @property
    def vocab_size(self) -> int:
        return self.tok.get_vocab_size(with_added_tokens=True) + len(self.special_tokens)

    def encode(self, text: str) -> list[int]:
        return [i + len(self.special_tokens) for i in self.tok.encode(text).ids]

    def decode(self, ids: list[int], *, skip_special: bool = True) -> str:
        normal = []
        specials = []
        for i in ids:
            if i in self._id_to_special:
                specials.append(self._id_to_special[i])
            else:
                normal.append(i - len(self.special_tokens))
        text = self.tok.decode(normal, skip_special_tokens=False)
        if skip_special or not specials:
            return text
        return text + "".join(f"<{s}>" for s in specials)

    def encode_with_special(self, prefix: str, suffix: str) -> tuple[list[int], int]:
        """Encode ``<bos> prefix <eos> suffix`` returning (ids, prompt_len)."""
        ids = [BOS, *self.encode(prefix), EOS, *self.encode(suffix)]
        return ids, len(ids) - len(self.encode(suffix))