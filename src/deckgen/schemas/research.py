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
    corroborated_by: int = 1  # independent websites stating the same thing (including this one)
    needs_review: bool = False  # strong claim resting on one non-official source: attribute it, and a human should check
    review_reason: str = ""
    tier: int = 1  # source tier: 4 primary/official, 3 reputable, 2 general reference, 1 blog/vendor/unknown, 0 noise
    origin_id: str = ""  # mirrors, republished copies and same-publisher pages collapse into one origin
    independent_origin_count: int = 1  # independent origins stating the same thing (self-sourced vendor pages do not count)
    self_sourced: bool = False  # a vendor-like page making claims about itself
    excluded: bool = False  # outweighed or contested: never used on slides, listed in review_before_use.md
    excluded_reason: str = ""
    contested: bool = False  # equal-weight conflict kept for hedged use: the slide says "sources differ"
    page_sig: list[int] = Field(default_factory=list)  # MinHash of the source page, for origin clustering


class ConflictRecord(_M):
    ids: list[str]
    claims: list[str]
    sources: list[str]
    status: Literal["resolved", "unresolved"]
    winner: str = ""
    reason: str = ""


class Findings(_M):
    findings: list[Finding]
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    passages: dict[str, list[str]] = Field(default_factory=dict)  # source url -> the question-relevant full passages the extractor read
    stats: dict[str, int] = Field(default_factory=dict)  # tool calls, pages, cache hits, flagged pages


class RawFinding(_M):
    """LLM extraction output (flat). Provenance fields are attached in code, never by the model."""
    claim: str
    evidence_snippet: str
    confidence: Literal["low", "medium", "high"]


class RawFindings(_M):
    findings: list[RawFinding] = Field(max_length=8)


class QueryPlan(_M):
    queries: list[str] = Field(min_length=1, max_length=3)


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
    data_hint: str = ""  # optional: candidate table/chart rows or comparable numbers found in the findings (ids), see research/shapes.py


class Outline(_M):
    arc: str
    deck_title: str
    slides: list[OutlineSlide] = Field(min_length=3)


class PairRef(_M):
    a: str
    b: str


class PairVerdicts(_M):
    """LLM output: which candidate pairs state the SAME fact with incompatible values."""
    contradictions: list[PairRef] = Field(default_factory=list)
