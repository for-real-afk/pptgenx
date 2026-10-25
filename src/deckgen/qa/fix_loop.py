"""QA orchestration with a never-worse guarantee.

Round 0 inspects the whole deck. Each fix round handles ONE issue phase (overflow, then overlap, then contrast, ...),
applies small diffs (trim/delete only, no net word growth), re-renders, re-inspects changed slides, and keeps the
result only if the blocking score improved. Otherwise it reverts to the best deck and stops.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

from deckgen.config import CONFIG
from deckgen.llm import LLM, Tracer
from deckgen.qa import checks, policy, soffice, validate_pptx, visual_critic
from deckgen.qa.slide_text import content_issues, extract
from deckgen.schemas import DeckSpec, SlideSpec
from deckgen.schemas.edits import Edit, EditList, EditRejected, apply_edits
from deckgen.schemas.qa import Issue, QAReport, RoundResult
from deckgen.spec.validate_spec import load_manifest, validate_spec


class Fixer(Protocol):
    def propose(self, slide: SlideSpec, issues: list[Issue]) -> list[Edit]: ...


class LLMFixer:
    """Proposes a small diff for one slide and one issue phase. It may only shorten or delete."""

    def __init__(self, llm: LLM):
        self.llm = llm

    def propose(self, slide: SlideSpec, issues: list[Issue]) -> list[Edit]:
        prompt = (
            "Fix the issues below by editing this slide's CONTENT so it fits its layout. Allowed edits only: "
            "replace a text field with a SHORTER version (fewer words), or delete one list item. "
            "Never add words. Keep every key fact. Make the fewest edits that resolve the issues.\n"
            "Paths are JSON pointers into the slide, e.g. /placeholders/body/1, /composition/items/2/body, /title.\n"
            f"SLIDE:\n{slide.model_dump_json(exclude_none=True)}\n"
            "ISSUES:\n" + "\n".join(f"- {i.type}/{i.severity}: {i.description} (idea: {i.suggested_fix})" for i in issues)
        )
        return self.llm.generate(stage="fixer", system="You propose minimal JSON edits to a slide. Return {\"edits\": [...]}.",
                                 prompt=prompt, schema=EditList).edits


def build(spec: DeckSpec, brand_dir: Path, out: Path) -> None:
    spec_path = out.with_suffix(".spec.json")
    spec_path.write_text(spec.model_dump_json(exclude_none=True, indent=1), encoding="utf-8")
    r = subprocess.run(["node", str(CONFIG.renderer_dir / "build_deck.js"), str(spec_path), str(brand_dir), str(out)],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"renderer failed: {r.stderr.strip()[:500]}")


def inspect(deck: Path, images: list[Path], numbers: list[int], llm: LLM, brand_dir: Path, *, consistency: bool) -> list[Issue]:
    """File + content + automated checks on the whole deck; critic only on `numbers`."""
    names = {l["name"] for l in load_manifest(brand_dir)["layouts"]}
    issues = [Issue(slide=1, type="other", severity="high", description=e, suggested_fix="fix in generator", source="file")
              for e in validate_pptx.validate(deck, names)]
    issues += content_issues(extract(deck))
    issues += checks.check(deck)
    if numbers:
        issues += visual_critic.critique(llm, [images[n - 1] for n in numbers], numbers, all_images=images if consistency else None)
    return issues


def _evaluate(spec, brand_dir, rd, llm, changed, prev: list[Issue], consistency: bool):
    rd.mkdir(parents=True, exist_ok=True)
    deck = rd / "deck.pptx"
    build(spec, brand_dir, deck)
    images = soffice.render_deck(deck, rd)
    fresh = inspect(deck, images, changed, llm, brand_dir, consistency=consistency)
    # critic verdicts for slides we did not re-inspect carry over; everything else was recomputed
    kept = [i for i in prev if i.source == "critic" and i.slide not in changed and not _is_consistency(i)]
    return fresh + kept


def _is_consistency(i: Issue) -> bool:
    return i.type == "inconsistent_style"


def run_qa(spec: DeckSpec, brand_dir: Path, run_dir: Path, llm: LLM, fixer: Fixer | None = None,
           max_rounds: int | None = None) -> tuple[DeckSpec, QAReport]:
    max_rounds = CONFIG.qa_max_rounds if max_rounds is None else max_rounds
    report = QAReport()
    if errs := validate_spec(spec, load_manifest(brand_dir), hard_only=True):
        report.file_errors = errs
        return spec, report
    all_slides = list(range(1, len(spec.slides) + 1))
    try:
        best_issues = _evaluate(spec, brand_dir, run_dir / "qa" / "round0", llm, all_slides, [], True)
    except soffice.RenderError as e:
        report.render_error = str(e)
        return spec, report
    best = spec
    report.rounds.append(RoundResult(round=0, inspected_slides=all_slides, issues=best_issues, score=policy.score(best_issues)))

    for rnd in range(1, max_rounds + 1):
        nxt = policy.next_phase(best_issues)
        if nxt is None:
            report.stopped = "no blocking issues"
            break
        if fixer is None:
            report.stopped = "blocking issues remain (no fixer)"
            break
        phase, hits = nxt
        by_slide: dict[int, list[Issue]] = {}
        for i in hits:
            by_slide.setdefault(i.slide, []).append(i)
        cand, changed, notes = best, [], []
        for n, iss in sorted(by_slide.items()):
            slide = cand.slides[n - 1]
            try:
                new = apply_edits(slide, fixer.propose(slide, iss))
            except (EditRejected, ValueError) as e:
                notes.append(f"s{n}: rejected ({str(e)[:80]})")
                continue
            if new != slide:
                slides = list(cand.slides)
                slides[n - 1] = new
                cand = cand.model_copy(update={"slides": slides})
                changed.append(n)
        label = f"fix {'/'.join(sorted(phase)) or 'other'} on slides {changed}" + (f"; {'; '.join(notes)}" if notes else "")
        if not changed:
            report.rounds.append(RoundResult(round=rnd, inspected_slides=[], issues=best_issues, action=label + " (no applicable edit)",
                                             accepted=False, score=policy.score(best_issues)))
            report.stopped = "fixer produced no acceptable edit"
            break
        try:
            new_issues = _evaluate(cand, brand_dir, run_dir / "qa" / f"round{rnd}", llm, changed, best_issues, False)
        except soffice.RenderError as e:
            report.render_error = str(e)
            break
        better = policy.score(new_issues) < policy.score(best_issues)
        report.rounds.append(RoundResult(round=rnd, inspected_slides=changed, issues=new_issues, action=label,
                                         accepted=better, score=policy.score(new_issues)))
        if not better:
            report.stopped = f"round {rnd} did not improve the score; reverted to best deck"
            break
        best, best_issues = cand, new_issues
    else:
        report.stopped = report.stopped or "round limit reached"

    report.remaining = best_issues
    tracer = getattr(llm, "tracer", None)
    if isinstance(tracer, Tracer):
        report.models = sorted(tracer.models)
    (run_dir / "qa_report.json").write_text(report.model_dump_json(indent=1), encoding="utf-8")
    (run_dir / "qa_report.md").write_text(report_markdown(report), encoding="utf-8")
    return best, report


def report_markdown(r: QAReport) -> str:
    out = ["# QA report", ""]
    if r.render_error:
        out.append(f"RENDER FAILED: {r.render_error}")
    out += [f"- file/spec error: {e}" for e in r.file_errors]
    out.append(f"Models used: {', '.join(r.models) or 'n/a'}. Stopped: {r.stopped or 'n/a'}.")
    for rr in r.rounds:
        out.append(f"\n## Round {rr.round}: {rr.action} -> {'kept' if rr.accepted else 'REVERTED'}; "
                   f"blocking score {rr.score[0]} ({rr.score[1]} issues); inspected {rr.inspected_slides}")
    blk = policy.blocking(r.remaining)
    non = [i for i in r.remaining if i not in blk]
    out.append(f"\n## Remaining blocking issues: {len(blk)}")
    out += [f"- s{i.slide} [{i.severity}] {i.type} ({i.source}): {i.description}" for i in blk]
    out.append(f"\n## Non-blocking notes (reported, never trigger fixes): {len(non)}")
    out += [f"- s{i.slide} [{i.severity}] {i.type} ({i.source}): {i.description}" for i in sorted(non, key=lambda i: i.slide)]
    return "\n".join(out)
