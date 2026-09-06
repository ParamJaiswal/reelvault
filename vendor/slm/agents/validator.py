"""ValidatorAgent: business-rule peer review of extracted records."""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from slm.agents.base import AgentResult, BaseAgent

_DATE_PATTERNS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d.%m.%Y")
_MONEY_RE = re.compile(r"^-?\d+(?:[.,]\d{1,2})?$")

MIN_YEAR = 1990
# "Physically possible" window: not from before 1990, not more than 10 years out.
MAX_FUTURE_YEARS = 10


class ValidatorAgent(BaseAgent):
    """Audits ExtractorAgent output against deterministic business rules.

    The heavy lifting is programmatic (fast, exact, CPU-cheap); the LLM role
    prompt documents the policy the rules encode. Checks:

    - record is a JSON object with at least one populated field
    - any parseable date lies within [1990, today + 10 years]
    - amount fields are well-formed money values within (-1e9, 1e9)
    - currency codes look like ISO-4217 (3 letters) when present
    - ``missing_data`` flag must be consistent: no missing_data=True while
      every field is populated (and vice versa for empty records)
    """

    name = "validator"
    role_key = "validator"

    def __init__(self, runtime, tokenizer, *, max_amount: float = 1_000_000.0) -> None:
        super().__init__(runtime, tokenizer)
        self.max_amount = max_amount

    # ------------------------------------------------------------- rule checks

    @staticmethod
    def _parse_date(value: str) -> _dt.date | None:
        for fmt in _DATE_PATTERNS:
            try:
                return _dt.datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
        return None

    def check(self, record: Any) -> list[str]:
        """Return a list of business-rule violations (empty == valid)."""
        problems: list[str] = []
        if not isinstance(record, dict):
            return ["record is not a JSON object"]

        populated = {
            k: v for k, v in record.items()
            if v not in (None, "", False) and not (
                isinstance(v, str) and not v.strip()
            )
        }
        if not populated:
            return ["record contains no populated fields"]

        for key, value in record.items():
            low = key.lower()

            # --- date plausibility -------------------------------------------
            if "date" in low and isinstance(value, str) and value.strip():
                parsed = self._parse_date(value)
                if parsed is None:
                    problems.append(f"{key}: unparseable date {value!r}")
                    continue
                today = _dt.date.today()
                floor = _dt.date(MIN_YEAR, 1, 1)
                ceiling = _dt.date(
                    today.year + MAX_FUTURE_YEARS, today.month, today.day
                )
                if not (floor <= parsed <= ceiling):
                    problems.append(
                        f"{key}: date {value!r} outside possible range "
                        f"[{MIN_YEAR}, today+{MAX_FUTURE_YEARS}y]"
                    )

            # --- money sanity --------------------------------------------------
            if ("amount" in low or "total" in low or "price" in low) and isinstance(value, str):
                value = value.strip()
                if value and value.lower() not in {"null", "none"}:
                    if not _MONEY_RE.match(value.replace(" ", "")):
                        problems.append(f"{key}: malformed money value {value!r}")
                        continue
                    try:
                        number = float(value.replace(",", "."))
                    except ValueError:
                        problems.append(f"{key}: unconvertible amount {value!r}")
                        continue
                    if not (0 < number <= self.max_amount):
                        problems.append(
                            f"{key}: amount {number} outside (0, {self.max_amount}]"
                        )

            # --- currency codes -------------------------------------------------
            if "currency" in low and isinstance(value, str) and value.strip():
                if not re.fullmatch(r"[A-Za-z]{3}", value.strip()):
                    problems.append(f"{key}: currency {value!r} is not a 3-letter code")

        # --- missing_data consistency ------------------------------------------
        flag = record.get("missing_data")
        if flag is True and len(populated) >= 4:
            problems.append(
                "missing_data=true but most fields are populated -- inconsistent"
            )
        if flag is False and all(
            v in (None, "") for k, v in record.items() if k != "missing_data"
        ):
            problems.append("missing_data=false but every other field is empty")
        return problems

    def _run(self, task: str, context: dict[str, Any]) -> AgentResult:
        record = context.get("record")
        if record is None:
            return AgentResult(self.name, False, error="no record provided to validate")
        problems = self.check(record)
        return AgentResult(
            agent=self.name,
            ok=not problems,
            output={"verdict": "pass" if not problems else "fail",
                    "violations": problems},
            reasons=problems,
        )