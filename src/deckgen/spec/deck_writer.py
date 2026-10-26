"""Whole-deck writing: ONE Sonnet call sees the approved outline, the facts sheet, every usable finding and the source passages for each slide,
and returns every slide as the same flat draft the per-slide writer uses (with cited finding ids). Each draft becomes a slide through the same
`finish_draft` (chart data filled by code, photo attached), then goes through the normal per-slide checks, where per-slide rewriting is the
repair for hard failures only. If the input is too large for one call the deck is written in two halves and the second half is shown the
first half's text. A draft that cannot be converted is simply left to the per-slide writer."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from deckgen.config import CONFIG
from deckgen.llm import LLM
from deckgen.research.photos import listing as photo_listing
from deckgen.schemas import SlideSpec
from deckgen.schemas.draft import SlideDraft
from deckgen.schemas.research import Brief, Finding, Outline, OutlineSlide
from deckgen.spec.claims import visible_fields
from deckgen.spec.spec_generator import SYSTEM as SLIDE_SYSTEM
from deckgen.spec.spec_generator import _layout_menu, _limits_menu, finish_draft, slide_source_text
from deckgen.spec.writer_prompt import stable_prefix, tags

NL = chr(10)
CHARS_PER_TOKEN = 3.0
MAX_INPUT_TOKENS = 120_000      # above this the deck is written in two halves
DECK_MAX_TOKENS = 16_000
SYSTEM = SLIDE_SYSTEM.replace("one presentation slide", "a whole presentation deck, one slide object per outline slide")


class DeckDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    slides: list[SlideDraft] = Field(default_factory=list)


def _outline_text(slides: list[OutlineSlide]) -> str:
    return NL.join(f"{o.n}. [{o.section}] hint={o.layout_hint}; purpose: {o.purpose}; key message: {o.key_message}; assigned findings: {', '.join(o.finding_ids)}"
                   + (f"; data: {o.data_hint}" if getattr(o, "data_hint", "") else "") for o in slides)


def _prompt(brief: Brief, outline: Outline, todo: list[OutlineSlide], pool: dict[str, Finding], passages: dict | None, photos, data, written: str) -> str:
    facts = NL.join(f"{f.id} [{tags(f)}] {f.claim} | evidence: {f.evidence_snippet}" for f in sorted(pool.values(), key=lambda f: (-f.tier, -f.independent_origin_count)))
    blocks = []
    for o in todo:
        src = slide_source_text(o, pool, passages)
        ph = photos.offered(o.n) if photos is not None and CONFIG.photos else []
        blocks.append(f"--- SLIDE {o.n} ---" + (f"{NL}CANDIDATE PHOTOS (layout PHOTO_TEXT + photo_id; at most 5 photo slides in the deck):{NL}{photo_listing(ph)}" if ph else "")
                      + (f"{NL}DATA SETS FOR A CHART (data_ref):{NL}{data.listing(o.finding_ids)}" if data is not None and data.offered else "")
                      + (f"{NL}SOURCE TEXT:{NL}{src}" if src else ""))
    return (f"DECK: {outline.deck_title} | audience: {brief.audience} | objective: {brief.objective}{NL}{NL}"
            f"APPROVED OUTLINE (all slides):{NL}{_outline_text(outline.slides)}{NL}{NL}"
            + (f"SLIDES ALREADY WRITTEN (text; stay consistent with them and do not repeat them):{NL}{written}{NL}{NL}" if written else "")
            + f"ALL USABLE FINDINGS (the only facts you may use; cite ids):{NL}{facts}{NL}{NL}"
            f"PER-SLIDE MATERIAL:{NL}{NL.join(blocks)}{NL}{NL}"
            f"Write slides {', '.join(str(o.n) for o in todo)} now, as the `slides` list, one object per slide with id s<NN> (two digits) and its own source_refs. "
            "The deck must read as one story: define every person, car or event before a later slide refers to it, and state each fact in the same words on every slide.")


def _est_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


def write_deck(llm: LLM, brief: Brief, outline: Outline, pool: dict[str, Finding], brand_dir: Path, passages: dict | None, facts_text: str,
               photos=None, data=None) -> dict[int, SlideSpec]:
    """{outline slide n: first version of the slide}. Slides that could not be drafted or converted are absent (the per-slide writer takes them)."""
    style = (brand_dir / "style_guide.md").read_text(encoding="utf-8")
    prefix = stable_prefix(style, _layout_menu(brand_dir), _limits_menu(), facts_text)
    todo = [o for o in outline.slides if not (o.n == 1 and o.layout_hint == "TITLE")]
    halves = [todo]
    if _est_tokens(prefix + _prompt(brief, outline, todo, pool, passages, photos, data, "")) > MAX_INPUT_TOKENS and len(todo) > 3:
        mid = len(todo) // 2
        halves = [todo[:mid], todo[mid:]]
    out: dict[int, SlideSpec] = {}
    written = ""
    by_n = {o.n: o for o in outline.slides}
    for part in halves:
        try:
            drafts = llm.generate(stage="spec_deck", system=SYSTEM, prompt=_prompt(brief, outline, part, pool, passages, photos, data, written), schema=DeckDraft,
                                  cache_prefix=prefix, max_tokens=DECK_MAX_TOKENS).slides
        except ValueError as e:  # unparseable or cut off: the remaining slides fall back to the per-slide writer
            print(f"deck writer: whole-deck call failed ({str(e)[:100]}); writing the rest slide by slide")
            return out
        for d in drafts:
            try:
                n = int(d.id.lstrip("s").lstrip("0") or 0)
            except ValueError:
                continue
            o = by_n.get(n)
            if o is None or n in out or o not in part:
                continue
            try:
                out[n] = finish_draft(d, o, photos, data)
            except ValueError:
                continue
        written = NL.join(f"SLIDE {n}: " + " | ".join(t for _, t in visible_fields(s)) for n, s in sorted(out.items()))
    return out
