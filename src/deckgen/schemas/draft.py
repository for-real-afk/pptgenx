"""Flat LLM-facing slide draft: no unions, no discriminators, one level of lists. Converted to the strict SlideSpec in code.

Gemini's structured output handles a subset of JSON Schema, so everything optional is an empty string / empty list
instead of a union with null. `draft_to_slide` is the only place the two models meet.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from deckgen.schemas.deck import (
    CardsComp, ChartSpec, ComparisonComp, IconRowsComp, SlideSpec, StatsComp, TableSpec, TimelineComp,
)

CompKind = Literal["none", "cards", "icon_rows", "stats", "timeline", "comparison"]
ChartType = Literal["none", "bar", "column", "line", "pie", "doughnut"]


class _Flat(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemDraft(_Flat):
    """One item of a composition. Fill only the fields the comp_kind uses; leave the rest empty."""
    icon: str = ""      # cards, icon_rows: react-icons name such as FaCarSide
    heading: str = ""   # cards, icon_rows, timeline, comparison
    body: str = ""      # cards, icon_rows, timeline
    value: str = ""     # stats: the big number
    label: str = ""     # stats
    detail: str = ""    # stats (optional)
    year: str = ""      # timeline
    bullets: list[str] = Field(default_factory=list)  # comparison


class SeriesDraft(_Flat):
    name: str
    values: list[float]


class RowDraft(_Flat):
    cells: list[str]


class SlideDraft(_Flat):
    id: str
    section: str
    layout: str
    title: str
    subtitle: str = ""                                   # TITLE / SECTION layouts
    bullets: list[str] = Field(default_factory=list)    # CHART_COMMENTARY / CLOSING / SOURCES body
    comp_kind: CompKind = "none"
    items: list[ItemDraft] = Field(default_factory=list)
    chart_type: ChartType = "none"
    chart_title: str = ""
    categories: list[str] = Field(default_factory=list)
    series: list[SeriesDraft] = Field(default_factory=list)
    table_header: list[str] = Field(default_factory=list)
    table_rows: list[RowDraft] = Field(default_factory=list)
    speaker_notes: str = ""
    source_refs: list[str] = Field(default_factory=list)


class DraftConversionError(ValueError):
    pass


def _composition(d: SlideDraft):
    k, it = d.comp_kind, d.items
    try:
        if k == "cards":
            return CardsComp(kind=k, items=[dict(icon=i.icon, heading=i.heading, body=i.body) for i in it])
        if k == "icon_rows":
            return IconRowsComp(kind=k, items=[dict(icon=i.icon, heading=i.heading, body=i.body) for i in it])
        if k == "stats":
            return StatsComp(kind=k, items=[dict(value=i.value, label=i.label, detail=i.detail or None) for i in it])
        if k == "timeline":
            return TimelineComp(kind=k, items=[dict(year=i.year, heading=i.heading, body=i.body) for i in it])
        if k == "comparison":
            return ComparisonComp(kind=k, items=[dict(heading=i.heading, bullets=i.bullets) for i in it])
    except ValidationError as e:
        raise DraftConversionError(f"{d.id}: {k} items invalid: {str(e)[:300]}") from e
    return None


def draft_to_slide(d: SlideDraft) -> SlideSpec:
    """Raises DraftConversionError with a message suitable for feeding back to the LLM."""
    placeholders: dict[str, str | list[str]] = {}
    if d.subtitle:
        placeholders["subtitle"] = d.subtitle
    if d.bullets:
        placeholders["body"] = d.bullets
    try:
        chart = None
        if d.chart_type != "none":
            chart = ChartSpec(type=d.chart_type, title=d.chart_title, categories=d.categories,
                              series=[s.model_dump() for s in d.series])
        table = None
        if d.table_header:
            table = TableSpec(header=d.table_header, rows=[r.cells for r in d.table_rows])
        return SlideSpec(id=d.id, section=d.section, layout=d.layout, title=d.title, placeholders=placeholders,
                         composition=_composition(d), chart=chart, table=table, speaker_notes=d.speaker_notes,
                         source_refs=d.source_refs)
    except ValidationError as e:
        raise DraftConversionError(f"{d.id}: {str(e)[:300]}") from e
