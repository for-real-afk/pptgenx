"""Schema + manifest checks: layout enum, placeholder names, per-placeholder max_chars."""
from __future__ import annotations

import json
from pathlib import Path

from deckgen.schemas import DeckSpec


def load_manifest(brand_dir: Path) -> dict:
    return json.loads((brand_dir / "manifest.json").read_text(encoding="utf-8"))


def validate_spec(spec: DeckSpec, manifest: dict) -> list[str]:
    """Return human-readable errors (empty = valid). Fed back to the spec LLM on retry."""
    layouts = {l["name"]: l for l in manifest["layouts"]}
    errs: list[str] = []
    for s in spec.slides:
        layout = layouts.get(s.layout)
        if not layout:
            errs.append(f"{s.id}: layout '{s.layout}' not in {sorted(layouts)}")
            continue
        phs = {p["name"]: p for p in layout["placeholders"]}
        if s.title.endswith("."):
            errs.append(f"{s.id}: title must not end with a period")
        title_max = phs.get("title", {}).get("max_chars")
        if title_max and len(s.title) > title_max:
            errs.append(f"{s.id}: title '{s.title}' is {len(s.title)} chars but layout {s.layout} allows {title_max}: remove at least {len(s.title) - title_max} chars")
        for name, val in s.placeholders.items():
            ph = phs.get(name)
            if not ph:
                errs.append(f"{s.id}: layout {s.layout} has no placeholder '{name}' (has {sorted(phs)})")
                continue
            text = " ".join(val) if isinstance(val, list) else val
            if ph["max_chars"] and len(text) > ph["max_chars"]:
                errs.append(f"{s.id}: placeholder '{name}' is {len(text)} chars but allows {ph['max_chars']}: remove at least {len(text) - ph['max_chars']} chars")
        if s.composition and s.layout != "TITLE_ONLY":
            errs.append(f"{s.id}: composition needs layout TITLE_ONLY")
        if s.chart and "chart" not in phs:
            errs.append(f"{s.id}: layout {s.layout} has no chart placeholder")
        if s.table and "table" not in phs:
            errs.append(f"{s.id}: layout {s.layout} has no table placeholder")
        if s.chart:
            n = len(s.chart.categories)
            for ser in s.chart.series:
                if len(ser.values) != n:
                    errs.append(f"{s.id}: series '{ser.name}' has {len(ser.values)} values for {n} categories")
    return errs
