"""Pydantic schemas for specialized extraction pipelines.
The SLM must emit JSON conforming to these; validation happens in code so a
malformed LLM answer can never poison the knowledge base."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class FactOut(BaseModel):
    field: str
    value: str
    quote: str = ""            # verbatim (or near-verbatim) source snippet


class BaseExtraction(BaseModel):
    summary: str = ""
    key_takeaways: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    facts: list[FactOut] = Field(default_factory=list)
    entities: list[dict] = Field(default_factory=list)  # {name, kind, quote?}

    @field_validator("categories")
    @classmethod
    def _cap_cats(cls, v):
        return [c.strip()[:40] for c in v if c and c.strip()][:6]


class JobFacts(BaseModel):
    company: str = ""
    role: str = ""
    job_type: str = ""          # internship | full-time | contract
    location: str = ""
    remote_status: str = ""
    experience_required: str = ""
    skills: str = ""
    salary: str = ""
    application_url: str = ""
    email: str = ""
    deadline: str = ""
    contact_person: str = ""


class EducationFacts(BaseModel):
    topic: str = ""
    difficulty: str = ""
    concepts: str = ""
    technologies: str = ""
    resources: str = ""
    next_steps: str = ""


class ToolFacts(BaseModel):
    tool_name: str = ""
    category: str = ""
    purpose: str = ""
    website: str = ""
    pricing: str = ""
    limitations: str = ""


class EventFacts(BaseModel):
    event_name: str = ""
    date: str = ""
    time: str = ""
    registration_url: str = ""
    location: str = ""
    organizer: str = ""


SCHEMA_FIELDS: dict[str, type[BaseModel]] = {
    "job": JobFacts,
    "education": EducationFacts,
    "tool": ToolFacts,
    "event": EventFacts,
}

VALID_CATEGORIES = [
    "Job", "Internship", "Career", "Tutorial", "Educational", "AI/ML",
    "Data Science", "Data Analytics", "Business", "Startup", "Tool",
    "Product", "News", "Finance", "Productivity", "Personal Advice", "Other",
]
