"""Repair pass on a finished deck: take the cross-slide checker's problems (contradictions, unexplained references, titles that promise more than the
slide shows) and fix them, using the facts sheet as the one authoritative wording.
  contradiction / unclear_reference: small diffs (the same fixer as the per-slide loop) that may add up to GROWTH words per slide, only for the fix.
  title_mismatch: the slide is rewritten (a chart slide included; its chart data is rebuilt by code from the findings).
This is a MANUAL tool (`deckgen repair`), not part of the default pipeline. The checker is re-run after each round; stop when no contradiction is left or after MAX_ROUNDS."""
from __future__ import annotations

import json
from pathlib import Path

from deckgen.llm import LLM
from deckgen.schemas import DeckSpec, SlideSpec
from deckgen.schemas.research import Brief, Findings, Outline
from deckgen.spec.deck_checks import Problem
from deckgen.spec.edit_loop import apply as apply_spec_edits
from deckgen.spec.edit_loop import check_all, edit_loop, propose, score
from deckgen.spec.spec_generator import SpecGenerationError, _deck_problems, pool_listing, slide_source_text, write_slide
from deckgen.spec.validate_spec import load_manifest
from deckgen.spec.violations import Violation, hard

MAX_ROUNDS = 2
GROWTH = 6
FIXABLE = ("contradiction", "unclear_reference", "title_mismatch")
NO_EDIT = ("TITLE", "SOURCES")


def counts(problems: list[Problem]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in problems:
        out[p.kind] = out.get(p.kind, 0) + 1
    return out


def _targets(p: Problem, slides: list[SlideSpec]) -> list[int]:
    """Slides to edit (1-based). A contradiction is edited on every non-protected slide it involves, so each moves to the sheet's wording."""
    ns = [n for n in p.slides if 1 < n <= len(slides) and slides[n - 1].layout not in NO_EDIT]
    return ns if p.kind == "contradiction" else ns[-1:]


def _rewrite(llm, s: SlideSpec, n: int, slides, problem: Violation, by_id, manifest, brief, outline, brand_dir, findings, facts_text, data) -> SlideSpec | None:
    """Full rewrite of one slide whose title does not match its body. Needs the outline slide with the same number."""
    if len(outline.slides) != len(slides) - 1 or n > len(outline.slides):
        return None
    o = outline.slides[n - 1]
    new = write_slide(llm, brief, o, outline, by_id, brand_dir, slides[:n - 1], n == len(outline.slides), [problem.message], passages=findings.passages,
                      facts_text=facts_text, data=data)
    return new.model_copy(update={"id": s.id, "section": s.section})


def repair_round(llm: LLM, slides: list[SlideSpec], problems: list[Problem], ctx: dict) -> tuple[list[SlideSpec], list[dict]]:
    by_id, manifest, findings = ctx["by_id"], ctx["manifest"], ctx["findings"]
    log: list[dict] = []
    slides = list(slides)
    for p in problems:
        if p.kind not in FIXABLE:
            continue
        for n in _targets(p, slides):
            s = slides[n - 1]
            src = slide_source_text(ctx["outline"].slides[n - 1], by_id, findings.passages) if n <= len(ctx["outline"].slides) else ""
            base = check_all(llm, s, by_id, slides[:n - 1], manifest, ctx["deck_title"], ctx["brand"], faith=False, source_text=src)
            problem = Violation("deck_contradiction" if p.kind != "unclear_reference" else "deck_unclear_reference",
                                f"{p.kind} involving slide(s) {p.slides}: {p.description}. Resolve it on THIS slide using only the findings and the facts sheet; "
                                f"where the sheet says sources differ, say so in the slide's own words.", "hard")
            entry = {"problem": p.kind, "slides": p.slides, "target": n, "description": p.description[:200]}
            try:
                if p.kind == "title_mismatch":
                    cand = _rewrite(llm, s, n, slides, problem, by_id, manifest, ctx["brief"], ctx["outline"], ctx["brand_dir"], findings, ctx["facts_text"], ctx["data"])
                    if cand is None:
                        entry["result"] = "no change (no matching outline slide to rewrite from)"
                        log.append(entry)
                        continue
                    entry["mode"] = "rewrite"
                else:
                    edits = propose(llm, s, base + [problem], by_id, pool_listing(by_id, 40), src, ctx["facts_text"], allow_growth=GROWTH)
                    cand = apply_spec_edits(s, edits, by_id, allow_growth=GROWTH)
                    entry["mode"] = "diff"
                after = check_all(llm, cand, by_id, slides[:n - 1], manifest, ctx["deck_title"], ctx["brand"], faith=False, source_text=src)
                if cand != s and score(after) <= score(base):
                    slides[n - 1] = cand
                    entry["result"] = "edited"
                else:
                    entry["result"] = "no change (proposal empty or made the slide worse)"
            except (SpecGenerationError, Exception) as e:  # noqa: BLE001 - one failed repair keeps the slide as it was; the problem stays listed
                entry["result"] = f"rejected: {str(e)[:120]}"
            log.append(entry)
    return slides, log


def repair_deck(llm: LLM, spec: DeckSpec, brief: Brief, outline: Outline, findings: Findings, brand_dir: Path, facts_text: str,
                sheet=None, data=None, rounds: int = MAX_ROUNDS) -> tuple[DeckSpec, dict]:
    by_id = {f.id: f for f in findings.findings if not f.excluded}
    ctx = {"by_id": by_id, "manifest": load_manifest(brand_dir), "findings": findings, "outline": outline, "brief": brief, "brand_dir": brand_dir,
           "facts_text": facts_text, "data": data, "deck_title": spec.title, "brand": spec.brand}
    slides = list(spec.slides)
    problems = _deck_problems(llm, slides, brief, outline, by_id)
    report = {"before": counts(problems), "rounds": [], "problems_before": [p.model_dump() for p in problems]}
    for r in range(1, rounds + 1):
        if not any(p.kind == "contradiction" for p in problems):
            break
        slides, log = repair_round(llm, slides, problems, ctx)
        problems = _deck_problems(llm, slides, brief, outline, by_id)
        report["rounds"].append({"round": r, "edits": log, "after": counts(problems)})
    report["after"] = counts(problems)
    report["unresolved"] = [p.model_dump() for p in problems]
    return spec.model_copy(update={"slides": slides}), report


def write_log(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
