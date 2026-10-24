"""QA orchestration: build -> file QA -> render -> checks + critic -> fix -> re-render, max N rounds.
Only slides whose spec changed are re-inspected by the critic after round 0."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Protocol

from deckgen.config import CONFIG
from deckgen.llm import LLM
from deckgen.qa import checks, soffice, validate_pptx, visual_critic
from deckgen.qa.slide_text import content_issues, extract
from deckgen.schemas import DeckSpec, SlideSpec
from deckgen.schemas.qa import Issue, QAReport, RoundResult
from deckgen.spec.validate_spec import load_manifest, validate_spec

BLOCKING = {"high", "medium"}


class Fixer(Protocol):
    def fix(self, slide: SlideSpec, issues: list[Issue]) -> SlideSpec: ...


class LLMFixer:
    """Edits slide CONTENT to fit the layout (never the reverse): shorten, drop items, add weight."""

    def __init__(self, llm: LLM):
        self.llm = llm

    def fix(self, slide: SlideSpec, issues: list[Issue]) -> SlideSpec:
        prompt = (
            "Revise this slide spec to resolve the QA issues. Edit content to fit the layout: shorten text, "
            "drop the weakest item, or add content weight to fill empty space. Keep every fact and the same id, "
            "section, layout and source_refs.\n"
            f"SLIDE:\n{slide.model_dump_json(exclude_none=True)}\n"
            "ISSUES:\n" + "\n".join(f"- {i.type}/{i.severity}: {i.description} (fix idea: {i.suggested_fix})" for i in issues)
        )
        return self.llm.generate(stage="fixer", system="You edit slide JSON. Return the full revised slide JSON only.",
                                 prompt=prompt, schema=SlideSpec)


def _hash(s: SlideSpec) -> str:
    return hashlib.sha256(s.model_dump_json().encode()).hexdigest()


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


def run_qa(spec: DeckSpec, brand_dir: Path, run_dir: Path, llm: LLM, fixer: Fixer | None = None,
           max_rounds: int | None = None) -> tuple[DeckSpec, QAReport]:
    max_rounds = CONFIG.qa_max_rounds if max_rounds is None else max_rounds
    report = QAReport()
    if errs := validate_spec(spec, load_manifest(brand_dir)):
        report.file_errors = errs
        return spec, report
    inspected: dict[int, str] = {}
    issues: list[Issue] = []
    for rnd in range(max_rounds + 1):
        rd = run_dir / "qa" / f"round{rnd}"
        rd.mkdir(parents=True, exist_ok=True)
        deck = rd / "deck.pptx"
        build(spec, brand_dir, deck)
        try:
            images = soffice.render_deck(deck, rd)
        except soffice.RenderError as e:
            report.render_error = str(e)
            break
        todo = [i for i, s in enumerate(spec.slides, 1) if inspected.get(i) != _hash(s)]
        kept = [i for i in issues if i.slide not in todo and i.severity not in BLOCKING and i.source == "critic"]
        issues = inspect(deck, images, todo, llm, brand_dir, consistency=(rnd == 0)) + ([] if rnd == 0 else kept)
        inspected.update({i: _hash(spec.slides[i - 1]) for i in todo})
        report.rounds.append(RoundResult(round=rnd, inspected_slides=todo, issues=issues))
        blocking = [i for i in issues if i.severity in BLOCKING]
        if not blocking or fixer is None or rnd == max_rounds:
            break
        by_slide: dict[int, list[Issue]] = {}
        for i in blocking:
            by_slide.setdefault(i.slide, []).append(i)
        slides = list(spec.slides)
        for n, iss in by_slide.items():
            slides[n - 1] = fixer.fix(slides[n - 1], iss)
        spec = spec.model_copy(update={"slides": slides})
    report.remaining = issues
    (run_dir / "qa_report.json").write_text(report.model_dump_json(indent=1), encoding="utf-8")
    (run_dir / "qa_report.md").write_text(report_markdown(report), encoding="utf-8")
    return spec, report


def report_markdown(r: QAReport) -> str:
    out = ["# QA report", ""]
    if r.render_error:
        out.append(f"RENDER FAILED: {r.render_error}")
    out += [f"- file/spec errors: {e}" for e in r.file_errors]
    for rr in r.rounds:
        out.append(f"## Round {rr.round}: inspected slides {rr.inspected_slides}, {len(rr.issues)} issues")
        out += [f"- s{i.slide} [{i.severity}] {i.type} ({i.source}): {i.description}" for i in rr.issues]
    out.append(f"\n## Remaining after final round: {len(r.remaining)}")
    out += [f"- s{i.slide} [{i.severity}] {i.type}: {i.description}" for i in r.remaining]
    return "\n".join(out)
