"""Multi-Agent Orchestration (Phase 9, Step 1).

Native, dependency-light agent abstractions over the existing SLM runtime --
no LangChain/CrewAI. Exports:

- ``BaseAgent``       -- role-prompted SLM wrapper with tracing + telemetry.
- ``ExtractorAgent``  -- structured extraction from noisy documents.
- ``ValidatorAgent``  -- business-rule peer review of extracted JSON.
- ``ActionAgent``     -- tool-call formatting/validation/execution.
- ``ExtractionTeam``  -- delegated Extractor -> Validator pipeline with
  bounded revision loop (internal peer review).
"""

from slm.agents.action import ActionAgent
from slm.agents.base import AgentResult, BaseAgent
from slm.agents.extractor import ExtractorAgent
from slm.agents.team import ExtractionTeam
from slm.agents.validator import ValidatorAgent

__all__ = [
    "ActionAgent",
    "AgentResult",
    "BaseAgent",
    "ExtractionTeam",
    "ExtractorAgent",
    "ValidatorAgent",
]