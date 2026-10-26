"""Writing-time rules for generated slides: capacity, provenance, number grounding, caps, variety.
Every failure is a Violation (registry id + level from the gate profile) with a message the writer model can act on."""
from __future__ import annotations

import json
import re
import subprocess
from functools import lru_cache

from deckgen.config import CONFIG
from deckgen.schemas import SlideSpec
from deckgen.schemas.research import Finding
from deckgen.spec import capacity
from deckgen.spec.claims import claim_violations
from deckgen.spec.violations import Violation, v

NO_SOURCE_LAYOUTS = {"TITLE", "SECTION", "SOURCES"}
SHORT_TITLE_OK = {"TITLE", "SECTION", "SOURCES", "CLOSING"}
NUM = re.compile(r"\d[\d,]*\.?\d*")


@lru_cache(maxsize=None)
def _pack_names(pack: str) -> frozenset[str]:
    js = f"try{{console.log(JSON.stringify(Object.keys(require('react-icons/{pack}'))))}}catch(e){{console.log('[]')}}"
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, cwd=CONFIG.root)
    return frozenset(json.loads(r.stdout or "[]"))


def icon_exists(name: str) -> bool:
    return name in _pack_names(name[:2].lower())


def _numbers(text: str) -> set[str]:
    return {m.group(0).replace(",", "").rstrip(".") for m in NUM.finditer(text)}


def _fmt(val: float) -> str:
    return str(int(val)) if float(val).is_integer() else f"{val:g}"


def check_slide_v(s: SlideSpec, findings: dict[str, Finding], prev: list[SlideSpec], extra: str = "") -> list[Violation]:
    e: list[Violation] = []
    n_words = lambda t: len(t.split())
    if s.layout not in SHORT_TITLE_OK and n_words(s.title) < 4:
        e.append(v("title_states_message", f"title '{s.title}' must state the message (at least 4 words)"))
    if s.layout not in SHORT_TITLE_OK and len(s.title) > capacity.title_limit():
        e.append(v("title_length", f"title '{s.title}' is {len(s.title)} chars; it must be at most {capacity.title_limit()}: "
                                   f"remove at least {len(s.title) - capacity.title_limit()} chars (aim for {capacity.title_aim()})"))

    # provenance
    if s.layout not in NO_SOURCE_LAYOUTS:
        if not s.source_refs:
            e.append(v("source_refs_present", "source_refs is empty: every content slide must cite finding ids"))
        for r in s.source_refs:
            if r not in findings:
                e.append(v("source_refs_present", f"source_refs cites unknown finding '{r}'"))
    cited = " ".join(f"{findings[r].claim} {findings[r].evidence_snippet}" for r in s.source_refs if r in findings)
    known_nums = _numbers(cited)

    # body bullets
    body = s.placeholders.get("body")
    if isinstance(body, list) and s.layout != "SOURCES":
        if s.layout in ("CHART_COMMENTARY", "PHOTO_TEXT"):
            nmax, cmax = capacity.commentary_limit()
        else:
            nmax, cmax = CONFIG.max_bullets, 400
        if len(body) > min(nmax, CONFIG.max_bullets):
            e.append(v("bullet_caps", f"body has {len(body)} bullets; max {min(nmax, CONFIG.max_bullets)}"))
        for b in body:
            if n_words(b) > CONFIG.max_words_per_bullet or len(b) > cmax:
                e.append(v("bullet_caps", f"bullet '{b[:40]}...' too long (max {CONFIG.max_words_per_bullet} words, {cmax} chars)"))
    if s.layout == "CLOSING" and isinstance(body, list):
        if len(body) > 4 or any(len(b) > 80 for b in body):
            e.append(v("bullet_caps", "CLOSING takes at most 4 bullets of at most 80 chars each"))

    # composition capacity + grounding
    c = s.composition
    if c:
        lim = capacity.limits(c.kind, len(c.items))
        for i, it in enumerate(c.items, 1):
            fields = it.model_dump(exclude_none=True)
            for f, val in fields.items():
                if f == "bullets":
                    mb = capacity.comparison_max_bullets()
                    if len(val) > mb:
                        e.append(v("composition_capacity", f"item {i}: {len(val)} bullets; max {mb}"))
                    for b in val:
                        if len(b) > lim["bullet"]:
                            e.append(v("composition_capacity", f"item {i} bullet '{b[:30]}...' is {len(b)} chars; max {lim['bullet']}"))
                elif f in lim and isinstance(val, str) and len(val) > lim[f]:
                    e.append(v("composition_capacity", f"item {i} {f} '{val[:40]}' is {len(val)} chars; max {lim[f]}: remove at least {len(val) - lim[f]} chars (cut words)"))
                if f == "icon" and not icon_exists(val):
                    e.append(v("icon_valid", f"item {i}: icon '{val}' does not exist in react-icons (use Fa* names)"))
            if c.kind == "stats":
                for num in _numbers(it.value):
                    if num not in known_nums:
                        e.append(v("stat_year_value_grounded", f"stat value '{it.value}' is not in the cited findings; numbers available: {sorted(known_nums)[:25]} (use one of these, or switch to cards or icon_rows)"))
            if c.kind == "timeline":
                for y in re.findall(r"\b\d{4}\b", it.year):
                    if y not in known_nums:
                        e.append(v("stat_year_value_grounded", f"timeline year {y} is not in the cited findings; years available: {sorted(n for n in known_nums if len(n) == 4)}"))
    if s.chart:
        ch = s.chart
        for ser in ch.series:
            for val in ser.values:
                if abs(val) < 10 and float(val).is_integer():
                    e.append(v("chart_rating_scale", f"chart value {_fmt(val)} looks like a rating on an invented scale; chart only real measured quantities from the findings (or use another layout)"))
                elif _fmt(val) not in known_nums:
                    e.append(v("stat_year_value_grounded", f"chart value {_fmt(val)} ({ser.name}) is not in the cited findings; numbers available: {sorted(known_nums)[:25]}"))
        if len(ch.categories) > 8:
            e.append(v("chart_table_caps", "chart has more than 8 categories"))
        if any(len(c_) > 22 for c_ in ch.categories):
            e.append(v("chart_table_caps", "chart category labels must be 22 chars or fewer"))
    if s.table:
        w = sum(len(x) for r in s.table.rows for x in r)
        if any(len(x) > 34 for r in s.table.rows for x in r) or w > 400:
            e.append(v("chart_table_caps", "table cells must be 34 chars or fewer"))
        if any(len(r) != len(s.table.header) for r in s.table.rows):
            e.append(v("chart_table_caps", "every table row must have as many cells as the header"))
    if s.layout == "CHART_COMMENTARY" and not s.chart:
        e.append(v("layout_needs_content", "CHART_COMMENTARY needs a chart"))
    if s.layout == "PHOTO_TEXT" and not (s.photo and isinstance(body, list) and body):
        e.append(v("layout_needs_content", "PHOTO_TEXT needs a photo (photo_id from the candidates offered for this slide) and 2-4 bullets"))
    if s.layout == "TABLE" and not s.table:
        e.append(v("layout_needs_content", "TABLE layout needs a table"))
    if s.layout == "TITLE_ONLY" and not c:
        e.append(v("layout_needs_content", "TITLE_ONLY needs a composition (cards, icon_rows, stats, timeline or comparison)"))

    e += claim_violations(s, findings, extra)

    # variety
    key = (s.layout, c.kind if c else None)
    if len(prev) >= 2 and all((p.layout, p.composition.kind if p.composition else None) == key for p in prev[-2:]):
        e.append(v("layout_variety", f"same layout/composition {key} used 3 slides in a row; choose a different one"))
    if not s.speaker_notes.strip() and s.layout not in NO_SOURCE_LAYOUTS:
        e.append(v("speaker_notes_present", "speaker_notes is empty: put detail there (2-4 sentences)"))
    return e


def check_slide(s: SlideSpec, findings: dict[str, Finding], prev: list[SlideSpec]) -> list[str]:
    """All messages, hard and warn (kept for tests and simple callers)."""
    return [x.message for x in check_slide_v(s, findings, prev)]
