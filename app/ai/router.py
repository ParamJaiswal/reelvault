"""Task Router: picks the cheapest capable model per pipeline task.

Policy (evidence-based, Aug 26 benchmarks):
  - classify      -> BusinessSLM if enabled+available, else Qwen
                     (shape-validated; any miss falls back to Qwen)
  - extract       -> Qwen (SLM measured 0.33 field accuracy on its own
                     golden set; revisit after reel-domain fine-tuning)
  - summarize     -> Qwen
  - assistant RAG -> Qwen
Deterministic work (URLs/emails/phones regex, deadline parsing, dedup,
radar scoring, validation) never touches an LLM.

Every decision is recorded in ai_runs via the providers themselves, and the
router annotates processing_events with which model served.
"""
from __future__ import annotations

import json
import logging

from app.ai import providers
from app.core.config import settings

log = logging.getLogger("rv.router")

from app.knowledge.schemas import VALID_CATEGORIES  # single source of truth

KNOWN_SCHEMAS = {"job", "education", "tool", "event", "generic"}

_CLS_SYSTEM = (
    "You classify Instagram Reels into categories for a personal knowledge base.\n"
    f"Pick 1-4 from exactly: {', '.join(VALID_CATEGORIES)}.\n"
    'Answer ONLY JSON: {"categories": [...], "primary_schema": '
    '"job|education|tool|event|generic"}\n'
    "Rules: advertising a specific opening -> Job or Internship. Teaching a "
    "skill/concept -> Tutorial or Educational. Recommending software/AI "
    "website -> Tool.\n"
    "Example 1:\n"
    'CAPTION: hiring post\n\nTRANSCRIPT:\n[00:00] Zylker is hiring data '
    'analyst interns in Bangalore, apply by September 15.\n\n'
    'ANSWER: {"categories": ["Job", "Internship"], "primary_schema": "job"}\n'
    "Example 2:\n"
    'CAPTION: tool shoutout\n\nTRANSCRIPT:\n[00:00] Whisper transcribes audio '
    'offline in 90 languages, free and open source.\n\n'
    'ANSWER: {"categories": ["Tool", "Productivity"], "primary_schema": "tool"}'
)


def _normalize_categories(raw_cats) -> list[str]:
    """Deterministic repair of common LLM misformats + alias mapping."""
    if isinstance(raw_cats, str):
        raw_cats = [raw_cats]
    if not isinstance(raw_cats, list):
        return []
    alias = {
        "ai/ml": "AI/ML", "ai": "AI/ML", "machine learning": "AI/ML",
        "ml": "AI/ML", "job": "Job", "jobs": "Job", "hiring": "Job",
        "internship": "Internship", "internships": "Internship",
        "career": "Career", "careers": "Career", "advice": "Personal Advice",
        "tutorial": "Tutorial", "tutorials": "Tutorial", "how-to": "Tutorial",
        "educational": "Educational", "education": "Educational",
        "learning": "Educational", "data science": "Data Science",
        "ds": "Data Science", "data analytics": "Data Analytics",
        "analytics": "Data Analytics", "da": "Data Analytics",
        "business": "Business", "startup": "Startup", "startups": "Startup",
        "tool": "Tool", "tools": "Tool", "product": "Product",
        "news": "News", "announcement": "News", "finance": "Finance",
        "money": "Finance", "investing": "Finance",
        "productivity": "Productivity", "other": "Other",
    }
    valid_ci = {v.lower(): v for v in VALID_CATEGORIES}
    out: list[str] = []
    for c in raw_cats:
        if not isinstance(c, str):
            continue
        # split "AI/ML, Career" style blobs
        for piece in c.replace(";", ",").split(","):
            key = piece.strip().lower().strip(".")
            if not key:
                continue
            mapped = alias.get(key)
            if not mapped:
                # exact match against the valid list (case-insensitive) so a
                # category the model returns verbatim is never silently
                # dropped just because it lacks an alias entry
                mapped = valid_ci.get(key)
            if mapped and mapped not in out:
                out.append(mapped)
    return out[:4]


SCHEMA_ALIAS = {"internship": "job", "job_opening": "job", "hiring": "job",
                "course": "education", "learning": "education",
                "tutorial": "education", "educational": "education",
                "data science": "education", "data analytics": "education",
                "career": "generic", "advice": "generic",
                "business": "generic", "finance": "generic",
                "startup": "generic", "productivity": "tool",
                "software": "tool", "app": "tool", "website": "tool",
                "meetup": "event", "webinar": "event", "news": "event"}


def _valid_classification(obj: dict) -> bool:
    cats = _normalize_categories(obj.get("categories"))
    schema = str(obj.get("primary_schema") or "").lower()
    schema = SCHEMA_ALIAS.get(schema, schema)
    return bool(cats) and schema in KNOWN_SCHEMAS


_KEYWORD_RULES = [  # deterministic fallback: (any-keywords) -> categories
    (("hiring", "we're hiring", "apply now", "openings", "job opening"), {"Job"}),
    (("internship", "intern role", "summer intern"), {"Internship"}),
    (("deadline", "last date", "apply before", "register before"), set()),
    (("tutorial", "how to", "step by step", "guide", "explained"),
     {"Tutorial", "Educational"}),
    (("rag", "llm", "embedding", "transformer", "neural", "prompt",
      "langchain", "agents"), {"AI/ML"}),
    (("pandas", "sql", "excel", "tableau", "power bi", "dashboard"),
     {"Data Analytics"}),
    (("tool", "app", "free tier", "pricing", "alternative to"),
     {"Tool", "Productivity"}),
    (("business idea", "startup idea", "revenue", "side hustle"),
     {"Business", "Startup"}),
    (("sip", "invest", "mutual fund", "stock", "rupee", "salary hike"),
     {"Finance"}),
    (("meetup", "event", "webinar", "conference"), {"News"}),
]


def heuristic_classify(text: str) -> dict:
    low = text.lower()
    cats: set = set()
    for kws, add in _KEYWORD_RULES:
        if any(k in low for k in kws):
            cats |= add
    if not cats:
        cats = {"Other"}
    schema = "generic"
    if "Job" in cats or "Internship" in cats:
        schema = "job"
    elif "Tutorial" in cats or "Educational" in cats or "AI/ML" in cats:
        schema = "education"
    elif "Tool" in cats:
        schema = "tool"
    elif "News" in cats:
        schema = "event"
    return {"categories": sorted(cats)[:4], "primary_schema": schema}


class TaskRouter:
    """Stateless; safe across threads."""

    # ---------------------------------------------------------- classify
    def classify(self, unified_text: str, reel_id: int | None = None) -> tuple[dict, str]:
        """Returns ({"categories": [...], "primary_schema": str}, served_by)."""
        messages = [{"role": "system", "content": _CLS_SYSTEM},
                    {"role": "user", "content": unified_text[:6000]}]

        if settings.slm_enabled:
            from app.ai.slm_provider import get_slm

            slm = get_slm()
            if slm.available():
                try:
                    raw = slm.chat(messages, max_tokens=64, json_mode=True,
                                   reel_id=reel_id)
                    obj = json.loads(raw) if raw else {}
                    if _valid_classification(obj):
                        log.info("router.classify served_by=business-slm")
                        return ({"categories": obj["categories"][:4],
                                 "primary_schema": obj["primary_schema"]},
                                "business-slm")
                    log.info("slm classification invalid -> qwen fallback")
                except Exception as e:  # noqa: BLE001
                    log.warning("slm classify failed (%s) -> qwen", e)

        llm = providers.get_llm()
        obj: dict = {}
        served_by = "qwen"
        for attempt in range(2):  # one strict retry on parse failure
            raw = llm.chat(messages, max_tokens=120,
                           temperature=0.1 if attempt == 0 else 0.4,
                           json_mode=True, reel_id=reel_id)
            try:
                obj = json.loads(raw)
            except Exception:
                import re

                m = re.search(r"\{.*\}", raw, re.DOTALL)
                try:
                    obj = json.loads(m.group(0)) if m else {}
                except Exception:
                    obj = {}
            if _valid_classification(obj):
                break
            served_by = "qwen(retry)" if attempt == 0 else "qwen(retry2)"
        cats = (_normalize_categories(obj.get("categories"))
                if isinstance(obj, dict) else [])
        if not cats:  # deterministic guardrail: never return nothing
            heur = heuristic_classify(unified_text)
            return ({**heur, "_note": "heuristic"}, served_by + "+rules")
        schema = str(obj.get("primary_schema") or "").lower()
        schema = SCHEMA_ALIAS.get(schema, schema)
        if schema not in KNOWN_SCHEMAS:
            heur = heuristic_classify(unified_text)
            return ({"categories": cats,
                     "primary_schema": heur["primary_schema"],
                     "_note": "schema-from-rules"},
                    served_by + "+rules")
        return ({"categories": cats, "primary_schema": schema}, served_by)

    # ----------------------------------------------------------- extract
    def extract(self, unified_text: str, schema_type: str,
                reel_id: int | None = None) -> dict:
        """Structured extraction. Qwen-only today by measurement."""
        llm = providers.get_llm()
        from app.knowledge.schemas import SCHEMA_FIELDS

        if schema_type != "generic" and schema_type in SCHEMA_FIELDS:
            fields = list(SCHEMA_FIELDS[schema_type].model_fields.keys())
            sys_p = (
                f"Extract structured data for a {schema_type.upper()} "
                "opportunity/content from an Instagram Reel transcript.\n"
                f"Fill ONLY these fields: {fields}.\n"
                "Output one fact for EVERY field above that the source "
                "clearly supports - do not stop after one or two facts; a "
                "field with no support in the source is simply omitted.\n"
                "CRITICAL: every non-empty value MUST be supported by a "
                "verbatim quote from the source.\n"
                'Return ONLY JSON: {"summary": "...", "key_takeaways": ["..."],'
                ' "action_items": ["..."], "categories": ["..."],'
                ' "facts": [{"field": "...", "value": "...", "quote": "..."}],'
                ' "entities": [{"name": "...", "kind": '
                '"company|tool|person|skill|location"}]}\n'
                "If a field isn't mentioned, omit it. Never invent URLs, "
                "salaries or dates.")
        else:
            sys_p = (
                'Summarize this Instagram Reel into knowledge. Return ONLY JSON:'
                ' {"summary": "...", "key_takeaways": ["..."],'
                ' "action_items": ["..."], "categories": ["..."],'
                ' "facts": [{"field":"...","value":"...","quote":"..."}],'
                ' "entities": [{"name": "...", "kind": "..."}]}')
        raw = llm.chat(
            [{"role": "system", "content": sys_p},
             {"role": "user", "content": unified_text[:7000]}],
            max_tokens=settings.llm_max_tokens, temperature=0.15,
            json_mode=True, reel_id=reel_id)
        try:
            return json.loads(raw)
        except Exception:
            import re

            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    pass
            return {}


_router: TaskRouter | None = None


def get_router() -> TaskRouter:
    global _router
    if _router is None:
        _router = TaskRouter()
    return _router
