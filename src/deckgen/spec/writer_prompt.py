"""Writer prompt, split for prompt caching: a STABLE prefix (style, layouts, limits, how to fill the JSON, layout choice, hedging rules)
that is identical for every slide and is marked cacheable, and a VARIABLE part (this slide, its findings, source text, the pool).

What worked in the hand-built comparison deck is behind config flags: the writer reads relevant full source passages, chooses the
layout per slide (the outline's layout_hint is only a suggestion), and hedges contested or weakly supported facts instead of dropping them."""
from __future__ import annotations

from deckgen.config import CONFIG
from deckgen.research.passages import words
from deckgen.schemas import SlideSpec
from deckgen.schemas.research import Brief, Finding, Outline, OutlineSlide

NL = chr(10)

HOW_TO_FILL = (
    "HOW TO FILL THE FLAT SLIDE JSON: set layout. TITLE/SECTION: title + subtitle. CLOSING: title + bullets (3-4 forward-looking "
    "points that are each supported by a cited finding; if the findings cannot support a forward-looking point, write FEWER bullets "
    "rather than inventing one). TITLE_ONLY: comp_kind in {cards (2,3,4 or 6 items: icon, heading, body), icon_rows (2-5 items: "
    "icon, heading, body), stats (2-4 items: value, label, optional detail), timeline (3-5 items: year, heading, body), "
    "comparison (exactly 2 items: heading, bullets)}; leave unused item fields empty. CHART_COMMENTARY: chart_type "
    "(bar/column/line/pie/doughnut), chart_title, data_ref (one of the DATA SETS offered for the slide; code fills the categories and values, so leave categories and series EMPTY and never type a chart value), bullets. TABLE: table_header + "
    "table_rows [{cells}]. PHOTO_TEXT (only when candidate photos are offered for the slide): photo_id (one offered id) + bullets (2-4); "
    "choose a photo only if its title shows what the slide is about, otherwise use another layout; never describe what a photo shows beyond "
    "what its title says. Always: id, section, title, speaker_notes (2-4 sentences with the detail and which source supports which "
    "claim), source_refs (ids of the findings you actually used).")

LAYOUT_CHOICE = (
    " LAYOUT CHOICE: the outline's layout_hint is only a suggestion. Choose, per slide, the layout and composition that fit the data: "
    "a TABLE or native chart for comparable numbers or a dated sequence, timeline items for events in order, stat callouts for 2-4 "
    "headline numbers, a two-column comparison for two sides, cards or icon rows otherwise. TITLE_ONLY, CHART_COMMENTARY and TABLE are "
    "all allowed on content slides; slide 1 stays TITLE and the last slide stays CLOSING. Vary layouts across the deck.")

TITLES = (
    " TITLES AND COHERENCE: every slide title must carry a specific claim or subject (a name, event, number or date); never reuse a generic label "
    "such as 'The peak' or 'The rise' across slides. Do not refer to an event ('the accident', 'the ban') unless an earlier slide explains it or "
    "this slide states it with its source. The closing slide must not refer to events no earlier slide explains.")

HEDGING = (
    " HEDGING: findings tagged CONTESTED (sources disagree) or REVIEW (one weak source) are not plain fact. Write them with hedged "
    "wording: 'approximately', 'reportedly', 'according to <source>' or 'sources differ (X vs Y)', never as an unqualified statement, "
    "and name the sources in speaker_notes. Findings with a higher tier and more independent origins win over weaker ones.")


def stable_prefix(style: str, menu: str, limits: str, facts_text: str = "") -> str:
    return (f"STYLE GUIDE:{NL}{style}{NL}{NL}LAYOUTS:{NL}{menu}{NL}{NL}FIELD LIMITS:{NL}{limits}{NL}{NL}"
            + HOW_TO_FILL + (LAYOUT_CHOICE if CONFIG.writer_chooses_layout else "") + TITLES + HEDGING + NL + NL + (facts_text + NL + NL if facts_text else ""))


def select_source_text(passages: dict[str, list[str]], urls: list[str], query: str, k: int = 6, max_chars: int = 7000) -> str:
    """The k passages most relevant to the slide, drawn from the pages behind its assigned findings (verbatim, with their url)."""
    q = set(words(query))
    pairs = [(u, p) for u in urls for p in passages.get(u, [])]
    scored = sorted(pairs, key=lambda up: -len(q & set(words(up[1]))))
    out, used = [], 0
    for u, p in scored[:k]:
        if used + len(p) > max_chars:
            break
        out.append(f"[{u}]{NL}{p}")
        used += len(p)
    return (NL + "---" + NL).join(out)


def tags(f: Finding) -> str:
    t = [f"t{f.tier}", f"o{f.independent_origin_count}"]
    if f.needs_review:
        t.append("REVIEW")
    if f.contested:
        t.append("CONTESTED with " + ",".join(f.conflicts_with))
    return " ".join(t)


def variable_part(brief: Brief, o: OutlineSlide, outline: Outline, preferred: list[Finding], pool_text: str, prev: list[SlideSpec],
                  is_last: bool, feedback: list[str], source_text: str = "", shapes_text: str = "", photo_text: str = "", data_text: str = "") -> str:
    recent = ", ".join(f"{p.layout}/{p.composition.kind if p.composition else '-'}" for p in prev[-3:]) or "none yet"
    used_before = sorted({r for p in prev for r in p.source_refs})
    facts = NL.join(f"{f.id} [{tags(f)}] {f.claim} | evidence: {f.evidence_snippet}" for f in preferred) or "(none)"
    hint = f"layout_hint={o.layout_hint} (a suggestion)" if CONFIG.writer_chooses_layout else f"layout_hint={o.layout_hint}"
    p = (
        f"DECK: {outline.deck_title} | audience: {brief.audience}{NL}"
        f"THIS SLIDE: n={o.n} id=s{o.n:02d} section='{o.section}' {hint} purpose: {o.purpose}{NL}"
        f"Key message: {o.key_message}{NL}Recent layouts used: {recent} (vary them).{NL}"
        + (f"Findings already relied on by earlier slides: {used_before} - do not build this slide's main claim on them.{NL}" if used_before else "")
        + (f"This is the closing slide: layout CLOSING.{NL}" if is_last else "")
        + (f"DATA HINT: {o.data_hint}{NL}" if getattr(o, "data_hint", "") else "")
        + (f"{NL}DATA SHAPES FOUND IN THE POOL (use one when it fits):{NL}{shapes_text}{NL}" if shapes_text and CONFIG.writer_chooses_layout else "")
        + (f"{NL}CANDIDATE PHOTOS FOR THIS SLIDE (real photographs, use layout PHOTO_TEXT with photo_id to show one; at most 5 photo slides in the deck):"
           f"{NL}{photo_text}{NL}" if photo_text else "")
        + (f"{NL}DATA SETS FOR A CHART (code builds the chart from one of these; choose CHART_COMMENTARY only if one fits):{NL}{data_text}{NL}" if data_text else "")
        + f"{NL}FINDINGS ASSIGNED TO THIS SLIDE (full detail):{NL}{facts}{NL}"
        + (f"{NL}SOURCE TEXT (verbatim passages from the pages behind these findings; the findings and these passages are the only facts you may "
           f"use; cite finding ids):{NL}{source_text}{NL}" if source_text and CONFIG.writer_source_text else "")
        + f"{NL}FULL FINDINGS POOL, best first (id [tier, independent origins] claim). Cite any of these you use; prefer concrete facts over filler:{NL}{pool_text}{NL}"
    )
    if feedback:
        p += f"{NL}Your previous attempt could not be used. Fix ALL of these:{NL}- " + (NL + "- ").join(feedback[:10])
    return p
