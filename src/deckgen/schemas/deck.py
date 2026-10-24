"""Strict DeckSpec: what the spec LLM emits and the Node renderer consumes. No coordinates, no OOXML."""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Card(_Strict):
    icon: str = Field(pattern=r"^[A-Z][a-z][A-Za-z0-9]+$", description="react-icons name, e.g. FaCarSide")
    heading: str
    body: str


class Stat(_Strict):
    value: str
    label: str
    detail: str | None = None


class Event(_Strict):
    year: str
    heading: str
    body: str


class Column(_Strict):
    heading: str
    bullets: list[str] = Field(min_length=1, max_length=5)


class CardsComp(_Strict):
    kind: Literal["cards"]
    items: list[Card] = Field(min_length=3, max_length=6)


class IconRowsComp(_Strict):
    kind: Literal["icon_rows"]
    items: list[Card] = Field(min_length=3, max_length=5)


class StatsComp(_Strict):
    kind: Literal["stats"]
    items: list[Stat] = Field(min_length=2, max_length=4)


class TimelineComp(_Strict):
    kind: Literal["timeline"]
    items: list[Event] = Field(min_length=3, max_length=5)


class ComparisonComp(_Strict):
    kind: Literal["comparison"]
    items: list[Column] = Field(min_length=2, max_length=2)


Composition = Annotated[
    Union[CardsComp, IconRowsComp, StatsComp, TimelineComp, ComparisonComp], Field(discriminator="kind")
]


class Series(_Strict):
    name: str
    values: list[float]


class ChartSpec(_Strict):
    type: Literal["bar", "column", "line", "pie", "doughnut"]
    title: str
    categories: list[str] = Field(min_length=2)
    series: list[Series] = Field(min_length=1)
    number_format: str | None = None


class TableSpec(_Strict):
    header: list[str] = Field(min_length=2, max_length=5)
    rows: list[list[str]] = Field(min_length=1, max_length=6)


class SlideSpec(_Strict):
    id: str
    section: str
    layout: str  # enum from brand manifest; checked in validate_spec
    title: str
    placeholders: dict[str, str | list[str]] = Field(default_factory=dict)
    composition: Composition | None = None
    chart: ChartSpec | None = None
    table: TableSpec | None = None
    speaker_notes: str = ""
    source_caption: str | None = None
    source_refs: list[str] = Field(default_factory=list)


class DeckSpec(_Strict):
    title: str
    brand: str
    slides: list[SlideSpec] = Field(min_length=1)
