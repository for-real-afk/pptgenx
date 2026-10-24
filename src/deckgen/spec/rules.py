"""Writing-time rules for generated slides: capacity, provenance, number grounding, caps, variety.
Every error is a sentence the spec LLM can act on."""
from __future__ import annotations

import json
import re
import subprocess
from functools import lru_cache

from deckgen.config import CONFIG
from deckgen.schemas import SlideSpec
from deckgen.schemas.research import Finding
from deckgen.spec import capacity
from deckgen.spec.claims import claim_errors

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


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def check_slide(s: SlideSpec, findings: dict[str, Finding], prev: list[SlideSpec]) -> list[str]:
    e: list[str] = []
    n_words = lambda t: len(t.split())
    if s.layout not in SHORT_TITLE_OK and n_words(s.title) < 4:
        e.append(f"title '{s.title}' must state the message (at least 4 words)")
    if s.layout not in SHORT_TITLE_OK and len(s.title) > capacity.title_limit():
        e.append(f"title '{s.title}' is {len(s.title)} chars; it must be at most {capacity.title_limit()}: remove at least {len(s.title) - capacity.title_limit()} chars (aim for {capacity.title_aim()})")

    # provenance
    if s.layout not in NO_SOURCE_LAYOUTS:
        if not s.source_refs:
            e.append("source_refs is empty: every content slide must cite finding ids")
        for r in s.source_refs:
            if r not in findings:
                e.append(f"source_refs cites unknown finding '{r}'")
    cited = " ".join(f"{findings[r].claim} {findings[r].evidence_snippet}" for r in s.source_refs if r in findings)
    known_nums = _numbers(cited)

    # body bullets
    body = s.placeholders.get("body")
    if isinstance(body, list) and s.layout != "SOURCES":
        if s.layout == "CHART_COMMENTARY":
            nmax, cmax = capacity.commentary_limit()
        else:
            nmax, cmax = CONFIG.max_bullets, 400
        if len(body) > min(nmax, CONFIG.max_bullets):
            e.append(f"body has {len(body)} bullets; max {min(nmax, CONFIG.max_bullets)}")
        for b in body:
            if n_words(b) > CONFIG.max_words_per_bullet or len(b) > cmax:
                e.append(f"bullet '{b[:40]}...' too long (max {CONFIG.max_words_per_bullet} words, {cmax} chars)")

    if s.layout == "CLOSING" and isinstance(body, list):
        if len(body) > 4 or any(len(b) > 80 for b in body):
            e.append("CLOSING takes at most 4 bullets of at most 80 chars each")

    # composition capacity + grounding
    c = s.composition
    if c:
        lim = capacity.limits(c.kind, len(c.items))
        for i, it in enumerate(c.items, 1):
            fields = it.model_dump(exclude_none=True)
            for f, v in fields.items():
                if f == "bullets":
                    mb = capacity.comparison_max_bullets()
                    if len(v) > mb:
                        e.append(f"item {i}: {len(v)} bullets; max {mb}")
                    for b in v:
                        if len(b) > lim["bullet"]:
                            e.append(f"item {i} bullet '{b[:30]}...' is {len(b)} chars; max {lim['bullet']}")
                elif f in lim and isinstance(v, str) and len(v) > lim[f]:
                    e.append(f"item {i} {f} '{v[:40]}' is {len(v)} chars; max {lim[f]}: remove at least {len(v) - lim[f]} chars (cut words)")
                if f == "icon" and not icon_exists(v):
                    e.append(f"item {i}: icon '{v}' does not exist in react-icons (use Fa* names)")
            if c.kind == "stats":
                for num in _numbers(it.value):
                    if num not in known_nums:
                        e.append(f"stat value '{it.value}' is not in the cited findings; numbers available: {sorted(known_nums)[:25]} (use one of these, or switch to cards or icon_rows)")
            if c.kind == "timeline":
                for y in re.findall(r"\b\d{4}\b", it.year):
                    if y not in known_nums:
                        e.append(f"timeline year {y} is not in the cited findings; years available: {sorted(n for n in known_nums if len(n) == 4)}")
    if s.chart:
        ch = s.chart
        for ser in ch.series:
            for v in ser.values:
                if abs(v) < 10 and float(v).is_integer():
                    e.append(f"chart value {_fmt(v)} looks like a rating on an invented scale; chart only real measured quantities from the findings (or use another layout)")
                elif _fmt(v) not in known_nums:
                    e.append(f"chart value {_fmt(v)} ({ser.name}) is not in the cited findings; numbers available: {sorted(known_nums)[:25]}")
        if len(ch.categories) > 8:
            e.append("chart has more than 8 categories")
        if any(len(c_) > 22 for c_ in ch.categories):
            e.append("chart category labels must be 22 chars or fewer")
    if s.table:
        w = sum(len(x) for r in s.table.rows for x in r)
        if any(len(x) > 34 for r in s.table.rows for x in r) or w > 400:
            e.append("table cells must be 34 chars or fewer")
        if any(len(r) != len(s.table.header) for r in s.table.rows):
            e.append("every table row must have as many cells as the header")
    if s.layout == "CHART_COMMENTARY" and not s.chart:
        e.append("CHART_COMMENTARY needs a chart")
    if s.layout == "TABLE" and not s.table:
        e.append("TABLE layout needs a table")
    if s.layout == "TITLE_ONLY" and not c:
        e.append("TITLE_ONLY needs a composition (cards, icon_rows, stats, timeline or comparison)")

    e += claim_errors(s, findings)

    # variety
    key = (s.layout, c.kind if c else None)
    if len(prev) >= 2 and all((p.layout, p.composition.kind if p.composition else None) == key for p in prev[-2:]):
        e.append(f"same layout/composition {key} used 3 slides in a row; choose a different one")
    if not s.speaker_notes.strip() and s.layout not in NO_SOURCE_LAYOUTS:
        e.append("speaker_notes is empty: put detail there (2-4 sentences)")
    return e
