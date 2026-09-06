"""ActionAgent: formats, validates, and executes tool calls."""

from __future__ import annotations

import json
import re
from typing import Any

from slm.agents.base import AgentResult, BaseAgent
from slm.model.constrained import generate_constrained_json
from slm.tools import ToolExecutor

_TOOL_HINT_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\b", re.IGNORECASE)


_TOOL_INTENT_RES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(
        r"\b(?:create|issue|raise|generate|file|submit|make)\s+(?:me\s+)?"
        r"(?:an?\s+)?invoice\b", re.IGNORECASE,
    ), "create_invoice"),
    (re.compile(r"\bescalate\b|\bhuman\s+(?:agent|operator|review)\b", re.IGNORECASE),
     "escalate_to_human"),
    (re.compile(r"\blog\s+(?:a\s+)?note\b", re.IGNORECASE), "log_note"),
]


class ActionAgent(BaseAgent):
    """Formats tool calls as strict JSON and validates them pre-execution.

    Works with a :class:`~slm.tools.ToolExecutor`: the agent picks the best
    matching registered tool for the request, produces schema-conformant JSON
    arguments (explicit payload > inline JSON > constrained decoding), and can
    execute through the sandbox.
    """

    name = "action"
    role_key = "action"

    def __init__(self, runtime, tokenizer, executor: ToolExecutor) -> None:
        super().__init__(runtime, tokenizer)
        self.executor = executor

    # ---------------------------------------------------------------- helpers

    def select_tool(self, task: str, hint: str | None = None) -> str | None:
        """Pick the registered tool matching the task intent (or explicit hint)."""
        if hint and hint in self.executor.registry:
            return hint
        for pattern, tool_name in _TOOL_INTENT_RES:
            if pattern.search(task) and tool_name in self.executor.registry:
                return tool_name
        # Fallback: exact tool-name token in the text ("create_invoice").
        words = {w.lower() for w in _TOOL_HINT_RE.findall(task)}
        for name in self.executor.registry.names():
            if name.lower() in words:
                return name
        return None

    def format_arguments(
        self, task: str, tool_name: str, payload: Any | None
    ) -> tuple[str | dict | None, str | None]:
        """Produce arguments for ``tool_name``; returns ``(args, error)``."""
        spec = self.executor.registry.get(tool_name)
        if spec is None:
            return None, "unknown_tool"
        if payload is not None:
            return payload, None
        inline = re.search(r"\{[^{}]*\}", task, re.DOTALL)
        if inline:
            try:
                parsed = json.loads(inline.group(0))
                if isinstance(parsed, dict):
                    return parsed, None
            except json.JSONDecodeError:
                pass
        ids = self._budget_ids(task, reserve=96)
        raw = generate_constrained_json(
            self.runtime.model, self.tokenizer, ids, spec.schema, max_new_tokens=96
        )
        return raw, None  # executor parses/validates strictly

    # ------------------------------------------------------------------ action

    def _run(self, task: str, context: dict[str, Any]) -> AgentResult:
        tool_name = context.get("tool") or self.select_tool(task)
        if not tool_name:
            return AgentResult(
                self.name, False,
                error="no_matching_tool",
                reasons=[f"registered tools: {self.executor.registry.names()}"],
            )
        args, err = self.format_arguments(task, tool_name, context.get("payload"))
        if err:
            return AgentResult(self.name, False, error=err)

        result = self.executor.execute_json(tool_name, args)
        return AgentResult(
            agent=self.name,
            ok=result.ok,
            output={
                "tool": result.tool,
                "status": "executed" if result.ok else "rejected",
                "output": result.output,
                "error": result.error,
                "detail": result.detail,
            },
            error=None if result.ok else result.error,
            meta={"args_preview": str(args)[:160]},
        )