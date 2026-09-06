"""Tool registry and safe execution sandbox (Phase 5, Step 2).

Exports:
- ``ToolRegistry``  -- registers Python functions with JSON schemas + risk levels.
- ``ToolSpec``      -- immutable description of a registered tool.
- ``ToolResult``    -- outcome envelope for every execution attempt.
- ``ToolExecutor``  -- validates SLM JSON output against the tool schema,
  executes safely, audits results, rejects unknown/unauthorized calls.
"""

from slm.tools.executor import ToolExecutor, ToolResult
from slm.tools.registry import RISK_LEVELS, ToolRegistry, ToolSpec, build_default_registry

__all__ = [
    "RISK_LEVELS",
    "ToolExecutor",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "build_default_registry",
]