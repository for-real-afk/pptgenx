"""What blocks a deck, what is only reported, and how decks are scored. One place, no scattered thresholds.

Blocking = overflow / cut-off, overlap, low contrast, footer collision (visual), plus file/content/automated
breakage. Spacing and alignment claims block only when the automated check confirms them on the same slide.
Everything else (empty space, style consistency, wrapping, ...) is reported but never triggers a fix round.
"""
from __future__ import annotations

from deckgen.schemas.qa import Issue

CRITIC_BLOCKING = {"overflow", "overlap", "low_contrast", "footer_collision"}
CRITIC_NEEDS_AUTO = {"tight_spacing", "misalignment"}  # blocking only if auto confirms on that slide
AUTO_CONFIRMS = {"overlap", "margin", "off_slide"}
AUTO_NONBLOCKING = {"empty_space"}
SEVERE = {"high", "medium"}

# Fix order: one phase per round, re-checked after each so a fix cannot hide a new problem elsewhere.
PHASES: list[set[str]] = [
    {"overflow", "off_slide", "font_floor", "excessive_wrap"},
    {"overlap", "footer_collision"},
    {"low_contrast"},
    {"margin", "tight_spacing", "misalignment", "placeholder_text", "other"},
]


def blocking(issues: list[Issue]) -> list[Issue]:
    auto_slides = {i.slide for i in issues if i.source == "auto" and i.type in AUTO_CONFIRMS}
    out = []
    for i in issues:
        if i.severity not in SEVERE:
            continue
        if i.source in ("file", "content"):
            out.append(i)
        elif i.source == "auto":
            if i.type not in AUTO_NONBLOCKING:
                out.append(i)
        elif i.type in CRITIC_BLOCKING or (i.type in CRITIC_NEEDS_AUTO and i.slide in auto_slides):
            out.append(i)
    return out


def score(issues: list[Issue]) -> tuple[int, int]:
    """Lower is better: (weighted severity, count) over blocking issues."""
    b = blocking(issues)
    return sum(3 if i.severity == "high" else 1 for i in b), len(b)


def next_phase(issues: list[Issue]) -> tuple[set[str], list[Issue]] | None:
    b = blocking(issues)
    for phase in PHASES:
        hit = [i for i in b if i.type in phase]
        if hit:
            return phase, hit
    return (set(), b) if b else None
