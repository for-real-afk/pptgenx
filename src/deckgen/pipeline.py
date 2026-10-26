"""End-to-end pipeline. Every stage writes its JSON under runs/<run_id>/ and is skipped on re-run unless forced."""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from deckgen import registry
from deckgen.config import CONFIG
from deckgen.llm import LLM, Tracer
from deckgen.planning import outliner, planner
from deckgen.qa import fix_loop, policy, soffice, validate_pptx
from deckgen.research.agent import run_research
from deckgen.research.tools import ResearchTools
from deckgen.research.weigh import TierTable
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
    tier_overrides: str | None = None  # name of config/tier_overrides/<name>.json (per-topic source tiers; config only)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def _write(path: Path, model) -> None:
    path.write_text(model.model_dump_json(exclude_none=True, indent=1), encoding="utf-8")


def _photo_catalog(llm, brief, outline, run_dir: Path, ledger, force: int):
    """Candidate photos per slide from Wikimedia Commons (cached in photos.json). A search failure means no photos, never a failed run."""
    from deckgen.research.photos import Catalog, find_for_outline

    path, dest = run_dir / "photos.json", run_dir / "photos"
    if path.exists() and STAGES.index("spec") < force:
        return Catalog.from_json(path.read_text(encoding="utf-8"), dest)
    if ledger:
        ledger.stage = "photos"
    try:
        cat = Catalog(find_for_outline(llm, brief, outline), dest)
    except Exception as e:  # noqa: BLE001 - photos are a bonus; the deck still builds without them
        print(f"photos: skipped ({type(e).__name__}: {str(e)[:100]})")
        cat = Catalog({}, dest)
    path.write_text(cat.to_json(), encoding="utf-8")
    if ledger:
        ledger.stage_line("photos")
    return cat


def run_pipeline(req: Request, llm: LLM, tools: ResearchTools, run_dir: Path, *, auto_approve: bool = False,
                 from_stage: str | None = None, do_qa: bool = True,
                 confirm: Callable | None = None, edit: Callable | None = None, ledger=None) -> dict:
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
        if ledger:
            ledger.stage = name
        out = make()
        timings[name] = round(time.time() - t0, 1)
        _write(path, out)
        if ledger:
            ledger.stage_line(name)
        return out

    brief = stage("plan", run_dir / "brief.json", Brief,
                  lambda: planner.plan(llm, req.topic, req.audience, req.slides, req.brand))
    findings = stage("research", run_dir / "findings.json", Findings, lambda: run_research(llm, tools, brief, TierTable(CONFIG.root / "config" / "tier_overrides" / f"{req.tier_overrides}.json" if req.tier_overrides else None)))
    if len(findings.findings) < max(6, brief.slide_count // 2):
        raise RuntimeError(f"research produced only {len(findings.findings)} findings; not enough for {brief.slide_count} slides")
    ids = {f.id for f in findings.findings if not f.excluded}
    outline = stage("outline", run_dir / "outline.json", Outline, lambda: outliner.approve(
        outliner.outline(llm, brief, findings), brief, ids, run_dir / "outline.json", auto_approve, confirm, edit))
    photos = _photo_catalog(llm, brief, outline, run_dir, ledger, force) if CONFIG.photos else None
    spec = stage("spec", run_dir / "deck_spec.json", DeckSpec, lambda: generate_spec(llm, brief, outline, findings, brand_dir, run_dir, photos=photos))

    qa_report: QAReport | None = None
    if do_qa:
        final_path = run_dir / "deck_spec.final.json"
        if final_path.exists() and STAGES.index("qa") < force:
            spec = DeckSpec.model_validate_json(final_path.read_text(encoding="utf-8"))
        else:
            t0 = time.time()
            if ledger:
                ledger.stage = "qa"
            spec, qa_report = fix_loop.run_qa(spec, brand_dir, run_dir, llm, fix_loop.LLMFixer(llm))
            timings["qa"] = round(time.time() - t0, 1)
            if ledger:
                ledger.stage_line("qa")
            _write(final_path, spec)
        if qa_report is None and (run_dir / "qa_report.json").exists():
            qa_report = QAReport.model_validate_json((run_dir / "qa_report.json").read_text(encoding="utf-8"))

    deck = run_dir / "deck.pptx"
    try:
        fix_loop.build(spec, brand_dir, deck)
    except RuntimeError as e:
        if "EBUSY" not in str(e) and "locked" not in str(e):
            raise
        deck = run_dir / f"deck-{time.strftime('%H%M%S')}.pptx"  # the old deck is open in PowerPoint: do not fight the lock
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
    with (run_dir / "sources.md").open("a", encoding="utf-8") as fh:
        fh.writelines(f"{chr(10)}- slide {i} photo: {sl.photo.alt} - {sl.photo.credit}" for i, sl in enumerate(spec.slides, 1) if sl.photo)
    tracer = getattr(llm, "tracer", None)
    notes_path = run_dir / "deck_notes.json"
    deck_notes = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.exists() else {}
    report = {
        "coverage": coverage(brief, findings.findings, spec), "quality": quality_metrics(spec, req.slides),
        "review_before_use": review_items(spec, by_id, deck_notes, findings),
        "shortfall": shortfall(req.slides, spec, deck_notes),
        "gate_profile": registry.active_profile(),
        "fallback_slides": len(deck_notes.get("fallbacks", [])),
        "redundancy": {"unresolved": sum(p["kind"] == "redundancy" for p in deck_notes.get("unresolved", [])),
                       "deleted": len(deck_notes.get("deleted_slides", []))},
        "deck_problems_unresolved": deck_notes.get("unresolved", []), "deck_problems_repaired_slides": deck_notes.get("repaired_slides", []),
        "run_id": run_dir.name, "topic": req.topic, "slides": len(spec.slides), "findings": len(findings.findings),
        "findings_used": len(used_ids), "research_stats": findings.stats, "stage_seconds": timings,
        "file_errors": validate_pptx.validate(deck, names), "images": len(images), "render_error": render_error,
        "deck": str(deck),
        "unsourced_slides": [s.id for s in spec.slides if s.layout not in NO_SOURCE and not s.source_refs],
        "invalid_layouts": [s.id for s in spec.slides if s.layout not in names],
        "first_attempt_rate": {st: tracer.first_attempt_rate(st) for st in tracer.attempts} if isinstance(tracer, Tracer) else {},
        "llm": tracer.summary() if isinstance(tracer, Tracer) else "",
        "roles": tracer.role_summary() if isinstance(tracer, Tracer) else {},
        "budget": llm.budget_report() if hasattr(llm, "budget_report") else {},
        "models": sorted(tracer.models) if isinstance(tracer, Tracer) else [],
        "cost": {"total_usd": round(ledger.total, 4), "budget_usd": ledger.budget} if ledger else None,
        "outline_auto_approved": bool(auto_approve),
        "qa_stopped": qa_report.stopped if qa_report else None,
        "qa_blocking_remaining": len(policy.blocking(qa_report.remaining)) if qa_report else None,
        "qa_rounds": [{"round": r.round, "action": r.action, "accepted": r.accepted, "score": r.score}
                      for r in (qa_report.rounds if qa_report else [])],
    }
    items = report["review_before_use"]
    (run_dir / "review_before_use.md").write_text("# Review before use" + chr(10)*2 + chr(10).join(f"- {x}" for x in items) + chr(10), encoding="utf-8")
    (run_dir / "run_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (run_dir / "run_report.md").write_text(report_md(report), encoding="utf-8")
    report["exported_to"] = str(export_outputs(run_dir, deck))
    return report


def review_items(spec: DeckSpec, by_id: dict, notes: dict, findings_obj=None) -> list[str]:
    """What a human should check before using the deck: shaky findings actually on slides, contested numbers, unresolved problems."""
    out = []
    if findings_obj is not None:
        for c in findings_obj.conflicts:
            out.append(f"CONFLICT {c.status} ({', '.join(c.ids)}): " + " VS ".join(f'"{t[:90]}" <{u}>' for t, u in zip(c.claims, c.sources))
                       + (f" -> kept {c.winner}" if c.winner else " -> excluded from slides, sources differ"))
        out += [f"excluded finding {f.id} ({f.source_url}): {f.excluded_reason} - \"{f.claim[:90]}\"" for f in findings_obj.findings
                if f.excluded and not any(f.id in c.ids for c in findings_obj.conflicts)]
    shaky: dict[str, dict] = {}  # reason -> {finding ids, slides}: one line per kind of problem, ids kept so nothing is hidden
    for i, s in enumerate(spec.slides, 1):
        for r in s.source_refs:
            f = by_id.get(r)
            if not f:
                continue
            why = "; ".join(x for x in (f.review_reason, "contradicts another finding (" + ", ".join(f.conflicts_with) + ")" if f.conflicts_with else "",
                                        "low confidence" if f.confidence == "low" else "", "source text contained instruction-like content" if f.flagged else "") if x)
            if why:
                g = shaky.setdefault(why, {"ids": {}, "slides": set()})
                g["ids"][r] = f.source_url
                g["slides"].add(i)
        if s.photo:
            out.append(f"slide {i}: photo '{s.photo.alt}' ({s.photo.credit}) was chosen from its Commons title only; check it shows what the slide says (file pages in photos.json)")
        if "built directly from the research findings" in s.speaker_notes.lower():
            out.append(f"slide {i}: fallback slide built from raw findings after generation failed; wording is plain by design")
    out += [f"{len(g['ids'])} finding(s) used on slides {sorted(g['slides'])}: {why} - ids {sorted(g['ids'])} (urls in findings.json)" for why, g in shaky.items()]
    out += [f"slide dropped [{u.get('category', 'evidence')}] ({u['message'][:70]}): {u['reason'][:140]}" for u in notes.get("dropped_unsupported_slides", [])]
    grouped: dict[str, list] = {}
    for w in notes.get("warnings", []):
        grouped.setdefault(w["check"], []).append(w)
    out += [f"warning [{c}] x{len(ws)} (outline slides {sorted({w['outline_slide'] for w in ws})}): {ws[0]['message'][:120]}" for c, ws in grouped.items()]
    out += [f"unresolved cross-slide problem on slides {p['slides']}: {p['description']}" for p in notes.get("unresolved", [])]
    return out


def shortfall(requested: int, spec: DeckSpec, notes: dict) -> dict:
    """Plain statement of how much shorter the deck is than asked, and why (drops by category, deleted duplicates)."""
    drops = notes.get("dropped_unsupported_slides", [])
    by_cat: dict[str, int] = {}
    for d in drops:
        by_cat[d.get("category", "evidence")] = by_cat.get(d.get("category", "evidence"), 0) + 1
    return {"requested": requested, "built": len(spec.slides), "dropped_by_category": by_cat,
            "deleted_as_redundant": len(notes.get("deleted_slides", [])), "warnings": len(notes.get("warnings", []))}


def quality_metrics(spec: DeckSpec, requested: int) -> dict:
    from deckgen.spec.claims import _is_concrete, visible_fields

    content = [s for s in spec.slides if s.layout not in ("TITLE", "SECTION", "SOURCES", "CLOSING")]
    stmts = [t for s in content for f, t in visible_fields(s) if f in ("body", "bullets", "detail", "label") and len(t.split()) >= 4]
    refs = [r for s in content for r in set(s.source_refs)]
    return {"slides_built": len(spec.slides), "slides_requested": requested,
            "specificity": round(sum(_is_concrete(t) for t in stmts) / len(stmts), 2) if stmts else None,
            "distinct_evidence": round(len(set(refs)) / len(refs), 2) if refs else None}


def export_outputs(run_dir: Path, deck: Path, label: str | None = None) -> Path:
    """Copy the finished deck and its companion files into <root>/outputs/<label>/ (a plain folder next to the code, outside runs/)."""
    import shutil

    import os

    dest = Path(os.environ.get("DECKGEN_OUTPUTS_DIR") or CONFIG.root / "outputs") / (label or run_dir.name)
    dest.mkdir(parents=True, exist_ok=True)
    for src in (deck, run_dir / "sources.md", run_dir / "review_before_use.md", run_dir / "run_report.md", run_dir / "qa_report.md"):
        if src.exists():
            shutil.copy2(src, dest / (("deck.pptx") if src == deck else src.name))
    images = run_dir / "final" / "images"
    if images.exists():
        shutil.copytree(images, dest / "images", dirs_exist_ok=True)
    return dest


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
            "- roles: " + "; ".join(f"{k}: {','.join(v['models'])} ({v['calls']} calls, {v['cache_hits']} cached, waited {v['waited_s']}s)" for k, v in r["roles"].items()),
            f"- budget use: {r['budget']}",
            f"- cost: {r['cost']}; outline auto-approved: {r.get('outline_auto_approved')}",
            f"- cross-slide problems repaired on slides {r['deck_problems_repaired_slides']}; unresolved: {r['deck_problems_unresolved'] or 'none'}",
            f"- shortfall: requested {r['shortfall']['requested']}, built {r['shortfall']['built']}; dropped by category {r['shortfall']['dropped_by_category'] or 'none'}; "
            f"deleted as redundant {r['shortfall']['deleted_as_redundant']}; warnings {r['shortfall']['warnings']}; gate profile '{r['gate_profile']}'",
            f"- quality: {r['quality']}" + ("   *** DECK IS MUCH SHORTER THAN REQUESTED: the evidence was too thin; see review list and coverage ***"
                                          if r["quality"]["slides_built"] < 0.6 * r["quality"]["slides_requested"] else ""),
            f"- REVIEW BEFORE USE ({len(r['review_before_use'])} items):"] + [f"  - {x}" for x in r["review_before_use"]] + [
            "- question coverage:"] + [f"  - {q}: {v}" for q, v in r["coverage"].items()]
    for q in r["qa_rounds"]:
        rows.append(f"  - round {q['round']}: {q['action']} -> {'kept' if q['accepted'] else 'reverted'}, score {q['score']}")
    return "\n".join(rows)
