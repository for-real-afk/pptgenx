from __future__ import annotations

import json
from pathlib import Path

from deckgen.llm import LLM
from deckgen.planning.arcs import ARCS
from deckgen.schemas.research import Brief, Findings, Outline

SYSTEM = "You are an editor who structures presentation decks. Output JSON only."


class OutlineError(RuntimeError):
    pass


def validate_outline(o: Outline, brief: Brief, ids: set[str]) -> list[str]:
    want = brief.slide_count - 1  # the Sources slide is appended in code
    errs = []
    if o.arc not in ARCS:
        errs.append(f"arc must be one of {sorted(ARCS)}")
    if len(o.slides) != want:
        errs.append(f"need exactly {want} slides (the Sources slide is added automatically), got {len(o.slides)}")
    if [s.n for s in o.slides] != list(range(1, len(o.slides) + 1)):
        errs.append("slide numbers must be 1..N in order")
    seen: list[str] = []
    for s in o.slides:
        if not seen or seen[-1] != s.section:
            if s.section in seen:
                errs.append(f"section '{s.section}' is not contiguous")
            seen.append(s.section)
        bad = [f for f in s.finding_ids if f not in ids]
        if bad:
            errs.append(f"slide {s.n} cites unknown findings {bad}")
        if 1 < s.n < len(o.slides) and not s.finding_ids:
            errs.append(f"slide {s.n} has no supporting findings")
    for sl in o.slides[1:-1]:
        allowed = ("TITLE_ONLY", "CHART_COMMENTARY", "TABLE") + (("SECTION",) if brief.slide_count >= 18 else ())
        if sl.layout_hint not in allowed:
            errs.append(f"slide {sl.n} layout_hint must be one of {allowed}")
    if o.slides and o.slides[0].layout_hint != "TITLE":
        errs.append("slide 1 layout_hint must be TITLE")
    if o.slides and o.slides[-1].layout_hint != "CLOSING":
        errs.append("last slide layout_hint must be CLOSING (conclusion and next steps)")
    return errs


def outline(llm: LLM, brief: Brief, findings: Findings, retries: int = 3) -> Outline:
    catalog = "\n".join(f"{f.id} [{f.confidence}] {f.claim}" for f in findings.findings)
    arcs = "\n".join(f"- {k}: {v}" for k, v in ARCS.items())
    ids = {f.id for f in findings.findings}
    want = brief.slide_count - 1
    base = (
        f"Topic: {brief.topic}\nAudience: {brief.audience}\nObjective: {brief.objective}\n\n"
        f"ARCS:\n{arcs}\n\nFINDINGS (the only facts you may build on):\n{catalog}\n\n"
        f"Choose the best arc and write a slide-by-slide outline of exactly {want} slides (a Sources slide is appended "
        "automatically). Slide 1 has layout_hint TITLE; the last slide has layout_hint CLOSING (conclusion and next steps). "
        "Other slides use layout_hint TITLE_ONLY (default; cards, stats, timeline, comparison or icon rows), CHART_COMMENTARY or TABLE; no section dividers. Each slide: n, section "
        "(contiguous groups, 3-5 sections total), purpose, key_message (one sentence stating the point), finding_ids "
        "(2-4 ids that support it; use the ids exactly). Cover the findings broadly; prefer high-confidence ones; use a "
        "CHART_COMMENTARY slide only where findings hold comparable numbers. Do not repeat the same message."
    )
    errs: list[str] = []
    for _ in range(retries + 1):
        prompt = base + (f"\n\nYour previous outline was rejected: {errs}. Fix these." if errs else "")
        o = llm.generate(stage="outline", system=SYSTEM, prompt=prompt, schema=Outline)
        errs = validate_outline(o, brief, ids)
        if not errs:
            return o
    raise OutlineError(f"outline invalid after {retries + 1} attempts: {errs}")


def render_outline(o: Outline) -> str:
    rows = [f"Arc: {o.arc}   Title: {o.deck_title}"]
    rows += [f"{s.n:>2}. [{s.section}] ({s.layout_hint}) {s.key_message}  <{', '.join(s.finding_ids)}>" for s in o.slides]
    return "\n".join(rows)


def approve(o: Outline, brief: Brief, ids: set[str], path: Path, auto: bool, confirm=None, edit=None) -> Outline:
    """Human checkpoint. `confirm(prompt)->str` and `edit(text)->str|None` are injectable for tests."""
    import typer

    confirm = confirm or (lambda p: typer.prompt(p, default="y"))
    edit = edit or (lambda text: typer.edit(text, extension=".json"))
    path.write_text(o.model_dump_json(indent=1), encoding="utf-8")
    if auto:
        return o
    while True:
        typer.echo("\n" + render_outline(o) + "\n")
        ans = confirm("Approve outline? [y]es / [e]dit JSON / [q]uit").strip().lower()
        if ans.startswith("y"):
            return o
        if ans.startswith("q"):
            raise typer.Abort()
        edited = edit(o.model_dump_json(indent=1))
        if edited:
            try:
                new = Outline.model_validate(json.loads(edited))
            except Exception as e:  # noqa: BLE001 - show any parse/validation problem and re-ask
                typer.echo(f"edit rejected: {e}")
                continue
            if errs := validate_outline(new, brief, ids):
                typer.echo(f"edit rejected: {errs}")
                continue
            o = new
            path.write_text(o.model_dump_json(indent=1), encoding="utf-8")
