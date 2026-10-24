"""End-to-end pipeline. Every stage writes its JSON under runs/<run_id>/ and is skipped on re-run unless forced."""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from deckgen.config import CONFIG
from deckgen.llm import LLM, Tracer
from deckgen.planning import outliner, planner
from deckgen.qa import fix_loop, policy, soffice, validate_pptx
from deckgen.research.agent import run_research
from deckgen.research.tools import ResearchTools
from deckgen.schemas import DeckSpec
from deckgen.schemas.qa import QAReport
from deckgen.schemas.research import Brief, Findings, Outline
from deckgen.spec.deck_checks import coverage
from deckgen.spec.spec_generator import domain, generate_spec

STAGES = ["plan", "research", "outline", "spec", "qa", "report"]
NO_SOURCE = ("TITLE", "SECTION", "SOURCES")


@dataclass
class Request:
    topic: str
    audience: str
    slides: int
    brand: str = "default_v1"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def _write(path: Path, model) -> None:
    path.write_text(model.model_dump_json(exclude_none=True, indent=1), encoding="utf-8")


def run_pipeline(req: Request, llm: LLM, tools: ResearchTools, run_dir: Path, *, auto_approve: bool = False,
                 from_stage: str | None = None, do_qa: bool = True,
                 confirm: Callable | None = None, edit: Callable | None = None) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    brand_dir = CONFIG.brands_dir / req.brand
    subprocess.run(["node", str(CONFIG.renderer_dir / "gen_manifest.js"), str(brand_dir)], check=True, capture_output=True)  # manifest can never lag layouts.js
    force = STAGES.index(from_stage) if from_stage else len(STAGES)
    timings: dict[str, float] = {}

    def stage(name: str, path: Path, model_cls, make):
        """Load the stage artifact if present (and not forced), else compute and persist it."""
        if path.exists() and STAGES.index(name) < force:
            return model_cls.model_validate_json(path.read_text(encoding="utf-8"))
        t0 = time.time()
        out = make()
        timings[name] = round(time.time() - t0, 1)
        _write(path, out)
        return out

    brief = stage("plan", run_dir / "brief.json", Brief,
                  lambda: planner.plan(llm, req.topic, req.audience, req.slides, req.brand))
    findings = stage("research", run_dir / "findings.json", Findings, lambda: run_research(llm, tools, brief))
    if len(findings.findings) < max(6, brief.slide_count // 2):
        raise RuntimeError(f"research produced only {len(findings.findings)} findings; not enough for {brief.slide_count} slides")
    ids = {f.id for f in findings.findings}
    outline = stage("outline", run_dir / "outline.json", Outline, lambda: outliner.approve(
        outliner.outline(llm, brief, findings), brief, ids, run_dir / "outline.json", auto_approve, confirm, edit))
    spec = stage("spec", run_dir / "deck_spec.json", DeckSpec, lambda: generate_spec(llm, brief, outline, findings, brand_dir, run_dir))

    qa_report: QAReport | None = None
    if do_qa:
        final_path = run_dir / "deck_spec.final.json"
        if final_path.exists() and STAGES.index("qa") < force:
            spec = DeckSpec.model_validate_json(final_path.read_text(encoding="utf-8"))
        else:
            t0 = time.time()
            spec, qa_report = fix_loop.run_qa(spec, brand_dir, run_dir, llm, fix_loop.LLMFixer(llm))
            timings["qa"] = round(time.time() - t0, 1)
            _write(final_path, spec)
        if qa_report is None and (run_dir / "qa_report.json").exists():
            qa_report = QAReport.model_validate_json((run_dir / "qa_report.json").read_text(encoding="utf-8"))

    deck = run_dir / "deck.pptx"
    fix_loop.build(spec, brand_dir, deck)
    images: list[Path] = []
    render_error = None
    try:
        images = soffice.render_deck(deck, run_dir / "final")
    except soffice.RenderError as e:
        render_error = str(e)
    names = {l["name"] for l in json.loads((brand_dir / "manifest.json").read_text(encoding="utf-8"))["layouts"]}

    by_id = {f.id: f for f in findings.findings}
    used_ids = sorted({r for s in spec.slides for r in s.source_refs})
    write_sources(run_dir / "sources.md", [by_id[i] for i in used_ids if i in by_id])
    tracer = getattr(llm, "tracer", None)
    notes_path = run_dir / "deck_notes.json"
    deck_notes = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.exists() else {}
    report = {
        "coverage": coverage(brief, findings.findings, spec),
        "deck_problems_unresolved": deck_notes.get("unresolved", []), "deck_problems_repaired_slides": deck_notes.get("repaired_slides", []),
        "run_id": run_dir.name, "topic": req.topic, "slides": len(spec.slides), "findings": len(findings.findings),
        "findings_used": len(used_ids), "research_stats": findings.stats, "stage_seconds": timings,
        "file_errors": validate_pptx.validate(deck, names), "images": len(images), "render_error": render_error,
        "deck": str(deck),
        "unsourced_slides": [s.id for s in spec.slides if s.layout not in NO_SOURCE and not s.source_refs],
        "invalid_layouts": [s.id for s in spec.slides if s.layout not in names],
        "first_attempt_rate": {st: tracer.first_attempt_rate(st) for st in tracer.attempts} if isinstance(tracer, Tracer) else {},
        "llm": tracer.summary() if isinstance(tracer, Tracer) else "",
        "models": sorted(tracer.models) if isinstance(tracer, Tracer) else [],
        "qa_stopped": qa_report.stopped if qa_report else None,
        "qa_blocking_remaining": len(policy.blocking(qa_report.remaining)) if qa_report else None,
        "qa_rounds": [{"round": r.round, "action": r.action, "accepted": r.accepted, "score": r.score}
                      for r in (qa_report.rounds if qa_report else [])],
    }
    (run_dir / "run_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (run_dir / "run_report.md").write_text(report_md(report), encoding="utf-8")
    return report


def write_sources(path: Path, used) -> None:
    lines = ["# Sources used", ""]
    for f in used:
        lines.append(f"- [{f.id}] {f.source_title or domain(f.source_url)} - {f.source_url} "
                     f"(retrieved {f.retrieved_at}; {f.source_type}; confidence {f.confidence})")
        lines.append(f"  - {f.claim}")
        if f.conflicts_with:
            lines.append(f"  - CONFLICTS with {', '.join(f.conflicts_with)}")
    path.write_text("\n".join(lines), encoding="utf-8")


def report_md(r: dict) -> str:
    rows = [f"# Run report: {r['run_id']}", "", f"Topic: {r['topic']}", "",
            f"- slides: {r['slides']}; findings: {r['findings']} ({r['findings_used']} used)",
            f"- research: {r['research_stats']}", f"- stage seconds: {r['stage_seconds']}",
            f"- unsourced slides (must be 0): {r['unsourced_slides']}",
            f"- invalid layout names (must be 0): {r['invalid_layouts']}",
            f"- file validation errors: {r['file_errors'] or 'none'}",
            f"- final images: {r['images']}" + (f" (render error: {r['render_error']})" if r["render_error"] else ""),
            f"- first-attempt schema pass rate by stage: {r['first_attempt_rate']}",
            f"- QA blocking issues remaining: {r['qa_blocking_remaining']} (stopped: {r['qa_stopped']})",
            f"- models: {r['models']}", f"- {r['llm']}",
            f"- cross-slide problems repaired on slides {r['deck_problems_repaired_slides']}; unresolved: {r['deck_problems_unresolved'] or 'none'}",
            "- question coverage:"] + [f"  - {q}: {v}" for q, v in r["coverage"].items()]
    for q in r["qa_rounds"]:
        rows.append(f"  - round {q['round']}: {q['action']} -> {'kept' if q['accepted'] else 'reverted'}, score {q['score']}")
    return "\n".join(rows)
