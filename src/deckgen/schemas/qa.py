"""Flat QA schemas (no unions, no nesting beyond a list of flat issues): safe for Gemini structured output."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IssueType = Literal[
    "overflow", "overlap", "footer_collision", "tight_spacing", "uneven_spacing", "empty_space",
    "misalignment", "low_contrast", "excessive_wrap", "broken_token", "inconsistent_style",
    "placeholder_text", "off_slide", "margin", "font_floor", "other",
]


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slide: int = Field(ge=1, description="1-based slide number")
    type: IssueType
    severity: Literal["high", "medium", "low"]
    description: str
    suggested_fix: str
    source: Literal["critic", "auto", "content", "file"] = "critic"


class CriticReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issues: list[Issue] = Field(default_factory=list)


class RoundResult(BaseModel):
    round: int
    inspected_slides: list[int]
    issues: list[Issue]
    action: str = "initial inspection"
    accepted: bool = True
    score: tuple[int, int] = (0, 0)  # (weighted severity, count) of blocking issues; lower is better


class QAReport(BaseModel):
    rounds: list[RoundResult] = Field(default_factory=list)
    remaining: list[Issue] = Field(default_factory=list)
    file_errors: list[str] = Field(default_factory=list)
    render_error: str | None = None
    models: list[str] = Field(default_factory=list)  # models that actually served QA calls
    stopped: str = ""
