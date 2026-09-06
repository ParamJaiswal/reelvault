"""NLI-style grounding gate: verify responses against retrieved evidence.

A programmatic entailment approximation tuned for short business answers.
A response passes only when two conditions hold:

1. **Lexical support** -- a sufficient fraction of the response's content
   tokens appear in the retrieved context (stopwords excluded).
2. **Numeric fidelity** -- every numeric literal in the response occurs in
   the context. Fabricated amounts/dates are the most damaging hallucination
   class in invoicing workflows, so they fail the gate outright.

Ungrounded responses are replaced with an explicit refusal instead of being
returned to the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from slm.rag.retriever import _tokenize

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")

DEFAULT_REFUSAL = (
    "I cannot answer this from the retrieved business records. "
    "Escalating to human review."
)

_STOPWORDS = frozenset(
    """
    the a an is are was were be been being to of and or in on for with it its
    this that these those as at by from has have had will would shall should
    can could may might must do does did not no nor so than then too very you
    your yours we our ours their theirs they them he she his her i me my mine
    please about into over under again further once here there when where why
    how all any both each few more most other some such only own same s t d ll
    m o re ve y ain aren couldn didn doesn hadn hasn haven isn ma mightn shan
    shouldn wasn weren won wouldn
    """.split()
)

# Tokens too generic to count as evidence even outside the stopword list.
_WEAK_TERMS = frozenset({"terms", "total", "amount", "invoice", "customer", "date"})


@dataclass(slots=True)
class GroundingResult:
    """Outcome of the grounding check."""

    grounded: bool
    support: float
    reasons: list[str] = field(default_factory=list)


class GroundingGate:
    """Heuristic NLI gate over (response, contexts) pairs."""

    def __init__(self, min_support: float = 0.4, *, strict_numbers: bool = True) -> None:
        if not 0.0 <= min_support <= 1.0:
            raise ValueError("min_support must be within [0, 1]")
        self.min_support = min_support
        self.strict_numbers = strict_numbers

    # ------------------------------------------------------------------- check

    @staticmethod
    def _content_tokens(text: str) -> list[str]:
        return [
            tok for tok in _tokenize(text)
            if tok not in _STOPWORDS and len(tok) >= 3
        ]

    def check(self, response: str, contexts: list[str]) -> GroundingResult:
        reasons: list[str] = []
        context_blob = " ".join(contexts).lower()
        context_tokens = set(self._content_tokens(context_blob))

        resp_content = [
            tok for tok in self._content_tokens(response)
            if tok not in _WEAK_TERMS or tok in context_tokens
        ]
        if not resp_content and not response.strip():
            return GroundingResult(False, 0.0, ["empty response"])

        hits = sum(1 for tok in resp_content if tok in context_tokens)
        support = hits / len(resp_content) if resp_content else 0.0
        if support < self.min_support:
            reasons.append(
                f"lexical support {support:.2f} below minimum {self.min_support:.2f}"
            )

        numbers_ok = True
        if self.strict_numbers:
            for num in _NUM_RE.findall(response):
                if num.lower() not in context_blob:
                    numbers_ok = False
                    reasons.append(f"number {num!r} absent from retrieved context")
                    break
        grounded = support >= self.min_support and numbers_ok
        return GroundingResult(grounded, round(support, 4), reasons)

    # ------------------------------------------------------------------- guard

    def guard(
        self,
        response: str,
        contexts: list[str],
        *,
        refusal: str = DEFAULT_REFUSAL,
    ) -> tuple[str, GroundingResult]:
        """Return ``(final_answer, result)`` -- refusal substituted when ungrounded."""
        result = self.check(response, contexts)
        return (response if result.grounded else refusal, result)