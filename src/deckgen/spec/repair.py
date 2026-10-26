"""Repair pass on a finished deck: take the cross-slide checker's problems (contradictions, unexplained references, titles that promise more than the
slide shows) and fix them with small diffs (the same fixer as the per-slide loop: shorter text, deleted items, never more words), using the facts sheet
as the one authoritative wording. The checker is re-run after each round; stop when no contradiction is left or after MAX_ROUNDS."""
from __future__ import annotations

import json
from pathlib import Path

from deckgen.llm import LLM
from deckgen.schemas import DeckSpec, SlideSpec
from deckgen.schemas.research import Brief, Findings, Outline
from deckgen.spec.deck_checks import Problem
from deckgen.spec.edit_loop import apply as apply_spec_edits
from deckgen.spec.edit_loop import check_all, propose, score
from deckgen.spec.spec_generator import _deck_problems, pool_listing
from deckgen.spec.validate_spec import load_manifest
from deckgen.spec.violations import Violation

MAX_ROUNDS = 2
FIXABLE = ("contradiction", "unclear_reference", "title_mismatch")
NO_EDIT = ("TITLE", "SOURCES")
CHART_HINT = (" The slide has a chart: show ONE value per subject (delete the duplicate categories AND the value at the same index in every series), "
              "take the value from the facts sheet, and replace the chart title with a shorter one that names the source.")


def counts(problems: list[Problem]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in problems:
        out[p.kind] = out.get(p.kind, 0) + 1
    return out


def _targets(p: Problem, slides: list[SlideSpec]) -> list[int]:
    """Slides to edit (1-based). A contradiction is edited on every non-protected slide it involves, so each moves to the sheet's wording."""
    ns = [n for n in p.slides if 1 < n <= len(slides) and slides[n - 1].layout not in NO_EDIT]
    return ns if p.kind == "contradiction" else ns[-1:]


def repair_round(llm: LLM, slides: list[SlideSpec], problems: list[Problem], by_id: dict, manifest: dict, deck_title: str, brand: str,
                 facts_text: str, passages: dict) -> tuple[list[SlideSpec], list[dict]]:
    log: list[dict] = []
    slides = list(slides)
    for p in problems:
        if p.kind not in FIXABLE:
            continue
        for n in _targets(p, slides):
            s = slides[n - 1]
            hint = CHART_HINT if s.chart else ""
            urls = sorted({by_id[r].source_url for r in s.source_refs if r in by_id})
            src = " ".join(x for u in urls for x in passages.get(u, [])[:2])[:5000]
            base = check_all(llm, s, by_id, slides[:n - 1], manifest, deck_title, brand, faith=False, source_text=src)
            problem = Violation("deck_contradiction" if p.kind != "unclear_reference" else "deck_unclear_reference",
                                f"{p.kind} involving slide(s) {p.slides}: {p.description}. Resolve it on THIS slide using only the findings and the facts sheet; "
                                f"where the sheet says sources differ, say so in the slide's own words. Do not add words.{hint}", "hard")
            entry = {"problem": p.kind, "slides": p.slides, "target": n, "description": p.description[:200]}
            try:
                edits = propose(llm, s, base + [problem], by_id, pool_listing(by_id, 40), src, facts_text)
                cand = apply_spec_edits(s, edits, by_id)
                after = check_all(llm, cand, by_id, slides[:n - 1], manifest, deck_title, brand, faith=False, source_text=src)
                if cand != s and score(after) <= score(base):
                    slides[n - 1] = cand
                    entry["result"] = "edited"
                else:
                    entry["result"] = "no change (proposal empty or made the slide worse)"
            except Exception as e:  # noqa: BLE001 - one failed repair keeps the slide as it was; the problem stays listed
                entry["result"] = f"rejected: {str(e)[:120]}"
            log.append(entry)
    return slides, log


def repair_deck(llm: LLM, spec: DeckSpec, brief: Brief, outline: Outline, findings: Findings, brand_dir: Path, facts_text: str,
                rounds: int = MAX_ROUNDS) -> tuple[DeckSpec, dict]:
    by_id = {f.id: f for f in findings.findings if not f.excluded}
    manifest = load_manifest(brand_dir)
    slides = list(spec.slides)
    problems = _deck_problems(llm, slides, brief, outline, by_id)
    report = {"before": counts(problems), "rounds": [], "problems_before": [p.model_dump() for p in problems]}
    for r in range(1, rounds + 1):
        if not any(p.kind == "contradiction" for p in problems):
            break
        slides, log = repair_round(llm, slides, problems, by_id, manifest, spec.title, spec.brand, facts_text, findings.passages)
        problems = _deck_problems(llm, slides, brief, outline, by_id)
        report["rounds"].append({"round": r, "edits": log, "after": counts(problems)})
    report["after"] = counts(problems)
    report["unresolved"] = [p.model_dump() for p in problems]
    return spec.model_copy(update={"slides": slides}), report


def write_log(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
