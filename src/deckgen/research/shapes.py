"""Data-shape detector (topic-independent): finds repeating patterns in the usable findings that suit a native chart or table.

chart  three or more findings state a number with the SAME unit for different named things (power in hp, price in dollars, ...)
table  four or more findings from one research question each carry a distinct year (a dated sequence of items)
Hints list finding ids; the writer may only use values that appear in the cited findings (the grounding rules enforce it)."""
from __future__ import annotations

import re
from collections import defaultdict

from pydantic import BaseModel, ConfigDict

from deckgen.schemas.research import Finding

UNITS = r"(hp|bhp|ps|kw|kg|km/h|kph|mph|%|percent|million|billion|trillion|units|cars|people|years|metres|meters|km|miles|tonnes|tons|seconds|usd|dollars|euros)"
NUM_UNIT = re.compile(rf"(\d[\d,]*\.?\d*)\s*{UNITS}(?![a-z])", re.I)
ENTITY = re.compile(r"^((?:[A-Z][\w\-]*\.?\s){1,4})")
YEAR = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    value: str
    finding_id: str


class DataHint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str  # "chart" | "table"
    unit: str = ""
    rows: list[Row]

    @property
    def finding_ids(self) -> list[str]:
        return sorted({r.finding_id for r in self.rows})

    def text(self) -> str:
        head = f"native chart candidate (unit: {self.unit})" if self.kind == "chart" else "table candidate (Year | What)"
        return head + ": " + "; ".join(f"{r.label} = {r.value} [{r.finding_id}]" for r in self.rows[:8]) + ". Use only these values; cite their ids."


def detect(findings: list[Finding]) -> list[DataHint]:
    usable = [f for f in findings if not f.excluded]
    hints: list[DataHint] = []
    by_unit: dict[str, dict[str, Row]] = defaultdict(dict)
    for f in usable:
        m, e = NUM_UNIT.search(f.claim), ENTITY.match(f.claim)
        if m and e:
            by_unit[m.group(2).lower()].setdefault(e.group(1).strip().lower(), Row(label=e.group(1).strip(), value=m.group(1).replace(",", ""), finding_id=f.id))
    for unit, rows in by_unit.items():
        if len(rows) >= 3 and len({r.value for r in rows.values()}) >= 2:
            hints.append(DataHint(kind="chart", unit=unit, rows=list(rows.values())[:8]))
    by_q: dict[str, dict[str, Row]] = defaultdict(dict)
    for f in usable:
        years = YEAR.findall(f.claim)
        if len(years) == 1:
            by_q[f.question].setdefault(years[0], Row(label=years[0], value=f.claim[:70], finding_id=f.id))
    for rows in by_q.values():
        if len(rows) >= 4:
            hints.append(DataHint(kind="table", rows=[rows[y] for y in sorted(rows)][:6]))
    return hints


def attach(outline_slides, hints: list[DataHint]):
    """Give each chart/table outline slide the best-matching hint (by overlap with its assigned findings, else an unused one)."""
    used: set[int] = set()
    out = []
    for s in outline_slides:
        kind = {"CHART_COMMENTARY": "chart", "TABLE": "table"}.get(s.layout_hint)
        if kind:
            cands = [(len(set(h.finding_ids) & set(s.finding_ids)), -i, i) for i, h in enumerate(hints) if h.kind == kind and i not in used]
            if cands:
                _, _, i = max(cands)
                used.add(i)
                s = s.model_copy(update={"data_hint": hints[i].text()})
        out.append(s)
    return out
