"""Stage artifacts: Brief -> Findings -> Outline. Each is written to runs/<id>/ as JSON."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


SourceType = Literal["official", "manufacturer", "archive", "journalism", "encyclopedia", "other"]


class Brief(_M):
    topic: str
    audience: str
    slide_count: int = Field(ge=4, le=40)
    brand: str = "default_v1"
    objective: str = ""
    research_questions: list[str] = Field(default_factory=list)


class QuestionPlan(_M):
    """LLM output of the planner."""
    objective: str
    research_questions: list[str] = Field(min_length=3, max_length=10)


class Finding(_M):
    id: str
    question: str
    claim: str
    evidence_snippet: str  # short, paraphrased or quoted; never a page dump
    source_type: SourceType
    source_url: str
    source_title: str = ""
    retrieved_at: str  # ISO-8601 UTC
    confidence: Literal["low", "medium", "high"]
    conflicts_with: list[str] = Field(default_factory=list)  # ids of findings that disagree
    flagged: bool = False  # source text contained suspected injected instructions


class Findings(_M):
    findings: list[Finding]
    stats: dict[str, int] = Field(default_factory=dict)  # tool calls, pages, cache hits, flagged pages


class RawFinding(_M):
    """LLM extraction output (flat). Provenance fields are attached in code, never by the model."""
    claim: str
    evidence_snippet: str
    confidence: Literal["low", "medium", "high"]


class RawFindings(_M):
    findings: list[RawFinding] = Field(max_length=8)


class QueryPlan(_M):
    queries: list[str] = Field(min_length=1, max_length=2)


class ConflictLink(_M):
    id: str
    conflicts_with: list[str]


class ConflictReport(_M):
    links: list[ConflictLink] = Field(default_factory=list)


class OutlineSlide(_M):
    n: int = Field(ge=1)
    section: str
    purpose: str
    key_message: str
    finding_ids: list[str] = Field(default_factory=list)
    layout_hint: str = ""


class Outline(_M):
    arc: str
    deck_title: str
    slides: list[OutlineSlide] = Field(min_length=3)
