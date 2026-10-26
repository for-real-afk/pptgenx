"""Deterministic chart data. The writer only picks a layout, a chart type, a title and a data set; code fills the categories and values from the
findings (via the shape detector), applies the facts sheet (one value per item; an item whose sources differ is left out rather than charted),
and records where every number came from in the speaker notes. The model never types a chart value."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from deckgen.research.facts import Sheet, _toks
from deckgen.research.shapes import DataHint, detect
from deckgen.schemas.draft import SeriesDraft, SlideDraft
from deckgen.schemas.research import Finding

NL = chr(10)
NUMBER = re.compile(r"\d[\d,]*\.?\d*")
MAX_CATEGORIES = 8
LABEL_MAX = 22


class DataSets:
    def __init__(self, findings: list[Finding], sheet: Sheet | None):
        self.by_id = {f.id: f for f in findings if not f.excluded}
        self.sheet = sheet
        self.offered: dict[str, DataHint] = {f"d{i}": h for i, h in enumerate([h for h in detect(list(self.by_id.values())) if h.kind == "chart"], 1)}

    def listing(self, finding_ids: list[str]) -> str:
        """Chart data sets, the ones overlapping the slide's findings first."""
        ranked = sorted(self.offered.items(), key=lambda kv: -len(set(kv[1].finding_ids) & set(finding_ids)))[:3]
        return NL.join(f"{k}: chart data, unit {h.unit}: " + "; ".join(f"{r.label} = {r.value}" for r in h.rows[:8]) for k, h in ranked)

    def _value(self, label: str, raw: str) -> tuple[float | None, str]:
        """(value, note). The facts sheet decides when it has an entry for this item: a resolved value replaces it, a tie drops the item."""
        if self.sheet:
            lt = _toks(label)
            for e in self.sheet.entries:
                st = _toks(e.subject)
                if st and lt and len(st & lt) / len(st) >= 0.6:
                    if e.status == "differ":
                        return None, f"{label}: left out, sources differ ({e.value})"
                    m = NUMBER.search(e.value)
                    if m:
                        return float(m.group(0).replace(",", "")), f"{label}: {m.group(0)} from the facts sheet ({e.source})"
        return float(raw.replace(",", "")), ""

    def fill(self, d: SlideDraft) -> SlideDraft:
        """Replace the draft's chart data with the data set it names. Raises ValueError (fed back to the writer) when it names none."""
        if d.chart_type == "none":
            return d
        hint = self.offered.get(d.data_ref)
        if hint is None:
            raise ValueError(f"a chart must use data_ref, one of {sorted(self.offered) or 'none (no chart data exists: use another layout)'}; leave categories and series empty")
        cats, vals, notes, refs = [], [], [], []
        for r in hint.rows:
            v, note = self._value(r.label, r.value)
            f = self.by_id.get(r.finding_id)
            if v is None:
                notes.append(note)
                continue
            label = r.label[:LABEL_MAX].strip()
            if label in cats:
                continue  # one value per item
            cats.append(label)
            vals.append(v)
            refs.append(r.finding_id)
            notes.append(note or f"{label}: {r.value} ({r.finding_id}, {urlparse(f.source_url).netloc.removeprefix('www.') if f else '?'})")
        if len(cats) < 2:
            raise ValueError("fewer than 2 items are left after the facts sheet; use another layout")
        cats, vals = cats[:MAX_CATEGORIES], vals[:MAX_CATEGORIES]
        return d.model_copy(update={"categories": cats, "series": [SeriesDraft(name=hint.unit, values=vals)], "source_refs": list(dict.fromkeys([*d.source_refs, *refs])),
                                    "speaker_notes": (d.speaker_notes.rstrip() + NL + "Chart data (taken from the findings, one value per item): " + "; ".join(notes)).strip()})
