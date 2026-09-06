"""ExtractorAgent: structured extraction from noisy business documents."""

from __future__ import annotations

import json
import re
from typing import Any

from slm.agents.base import AgentResult, BaseAgent
from slm.model.constrained import generate_constrained_json

DEFAULT_EXTRACTION_SCHEMA: dict[str, str] = {
    "invoice_id": "str",
    "customer": "str",
    "amount": "str",
    "currency": "str",
    "date": "str",
    "missing_data": "bool",
}

# Cheap noise patterns stripped before the text reaches the model.
_NOISE_RE = re.compile(
    r"(?:^|\n)\s*(?:from|sent|subject|cc|bcc)\s*:.*?(?=\n|$)", re.IGNORECASE
)


class ExtractorAgent(BaseAgent):
    """Dedicated to pulling strict JSON out of messy invoices/emails."""

    name = "extractor"
    role_key = "extractor"

    def __init__(self, runtime, tokenizer, schema: dict[str, str] | None = None) -> None:
        super().__init__(runtime, tokenizer)
        self.schema = dict(schema) if schema else dict(DEFAULT_EXTRACTION_SCHEMA)

    @staticmethod
    def denoise(text: str) -> str:
        """Strip email headers and collapse whitespace before encoding."""
        cleaned = _NOISE_RE.sub(" ", text)
        return re.sub(r"\s{2,}", " ", cleaned).strip()

    def _run(self, task: str, context: dict[str, Any]) -> AgentResult:
        document = context.get("document", task)
        clean = self.denoise(document)
        ids = self._budget_ids(clean, reserve=200)
        raw = generate_constrained_json(
            self.runtime.model,
            self.tokenizer,
            ids,
            self.schema,
            max_new_tokens=192,
        )
        data = json.loads(raw)  # state machine guarantees validity
        return AgentResult(
            agent=self.name,
            ok=True,
            output=data,
            meta={"schema": list(self.schema), "denoised_chars": len(clean)},
        )