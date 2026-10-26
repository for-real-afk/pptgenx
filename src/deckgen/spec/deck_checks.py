"""Deck-level checks that no single-slide check can make: contradictions between slides, repeated points,
and whether every planned research question ended up on a slide (or is dropped with a stated reason)."""
from __future__ import annotations

import re
from itertools import combinations
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from deckgen.llm import LLM
from deckgen.schemas import DeckSpec
from deckgen.schemas.research import Brief, Finding
from deckgen.spec.claims import visible_fields

SKIP = ("TITLE", "SECTION", "SOURCES", "CLOSING")


NOISE = re.compile("(this is fine|that is fine|is fine,|no issue|not a contradiction|not actually|no real (problem|contradiction)|is consistent|are consistent|however,? (this|that|it) is)", re.I)


class Quote(BaseModel):
    model_config = ConfigDict(extra="ignore")
    slide: int
    text: str = Field(description="words copied EXACTLY from that slide")


class Problem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quotes: list[Quote] = Field(default_factory=list)
    kind: Literal["contradiction", "redundancy", "unclear_reference", "title_mismatch"]
    slides: list[int] = Field(min_length=1, max_length=14, description="1-based slide numbers involved")
    description: str


class ProblemList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    problems: list[Problem] = Field(default_factory=list)


def slide_texts(spec: DeckSpec) -> dict[int, str]:
    return {i: " | ".join(t for _, t in visible_fields(s)) for i, s in enumerate(spec.slides, 1) if s.layout not in ("SOURCES",)}


def _units(spec: DeckSpec) -> list[tuple[int, str]]:
    """Small statements (bullets, card bodies, ...) to compare across slides."""
    out = []
    for i, s in enumerate(spec.slides, 1):
        if s.layout in SKIP:
            continue
        out += [(i, t) for f, t in visible_fields(s) if f not in ("title", "heading", "year", "value") and len(t.split()) >= 5]
    return out


def _tok(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 3}


def redundancy(spec: DeckSpec, threshold: float = 0.6) -> list[Problem]:
    seen: set[tuple[int, int]] = set()
    out = []
    for (i, a), (j, b) in combinations(_units(spec), 2):
        if i == j or (i, j) in seen:
            continue
        ta, tb = _tok(a), _tok(b)
        if ta and tb and len(ta & tb) / len(ta | tb) >= threshold:
            seen.add((i, j))
            out.append(Problem(kind="redundancy", slides=[i, j], description=f"slide {j} repeats slide {i}: '{b[:70]}'"))
    return out


def shared_evidence(spec: DeckSpec) -> list[Problem]:
    """Two content slides resting on the same findings make the same point twice."""
    out = []
    content = [(i, set(s.source_refs)) for i, s in enumerate(spec.slides, 1) if s.layout not in SKIP]
    for (i, a), (j, b) in combinations(content, 2):
        if len(a & b) >= 2:
            out.append(Problem(kind="redundancy", slides=[i, j], description=f"slides {i} and {j} rest on the same findings {sorted(a & b)}"))
    return out


def coverage(brief: Brief, findings: list[Finding], spec: DeckSpec) -> dict[str, str]:
    """question -> 'covered by slides [..]' or the reason it is not on any slide."""
    where = {}
    for q in brief.research_questions:
        fs = [f for f in findings if f.question == q]
        slides = sorted({i for i, s in enumerate(spec.slides, 1) if any(f.id in s.source_refs for f in fs)})
        if slides:
            where[q] = f"covered by slides {slides}"
        elif not fs:
            where[q] = "dropped: research found no usable findings for this question"
        elif all(f.confidence == "low" for f in fs):
            where[q] = "dropped: only low-confidence findings"
        else:
            where[q] = f"dropped: {len(fs)} findings exist but the outline did not use them within {len(spec.slides) - 1} slides"
    return where


def consistency(llm: LLM, spec: DeckSpec, findings: dict[str, Finding]) -> list[Problem]:
    texts = "\n".join(f"SLIDE {i}: {t}" for i, t in slide_texts(spec).items())
    cited = sorted({r for s in spec.slides for r in s.source_refs})
    facts = "\n".join(f"{r}: {findings[r].claim}" for r in cited if r in findings)
    prompt = (
        f"SLIDES (visible text):\n{texts}\n\nFINDINGS cited by the deck:\n{facts}\n\n"
        "Find problems BETWEEN slides: (1) contradiction: two slides state incompatible facts, or the same quantity with "
        "different values (for example two different power figures, or different accounts of who withdrew); (2) unclear_reference: "
        "a statement that cannot be understood without context the deck never gives (for example 'the accident' with no earlier "
        "mention); (3) redundancy: the same point made on more than one slide; (4) title_mismatch: a slide whose title promises something its content does not deliver (a count, a scope, a claim the body does not state). Only report real problems; return an empty list "
        "if there are none. For each problem give the slide numbers, one sentence, and `quotes`: for every slide involved, the exact words copied from "
        "that slide that show the problem. A problem without exact quotes, or one you conclude is fine, must not be listed."
    )
    try:
        found = llm.generate(stage="deck_consistency", system="You review a whole deck for internal consistency. Output JSON only.",
                             prompt=prompt, schema=ProblemList).problems
    except ValueError as e:  # an unparseable review must not lose the deck: logged, the deterministic checks still run
        print(f"deck consistency check unparseable ({str(e)[:100]}); skipped")
        return []
    return [p for p in found if keep(p, slide_texts(spec))]


def _flat(t: str) -> str:
    return " ".join(re.sub(r"[^\w%.,'-]+", " ", t.lower()).split())


def keep(p: Problem, texts: dict[int, str]) -> bool:
    """Checker noise filter: a problem counts only if it quotes words that really appear on the slide(s) it names, and is not a remark that
    says the thing is fine after all."""
    if NOISE.search(p.description) or not p.quotes:
        return False
    return all(_flat(q.text) and _flat(q.text) in _flat(texts.get(q.slide, "")) for q in p.quotes)
