"""Run orchestration behind the CLI commands (generate / repair / fixpass). The CLI only parses options and prints; everything that decides what
happens in a run lives here, with output going through an injected `echo` so it can be tested without typer.

Shared pieces (single responsibility each): `make_ledger`, `budget_guard` (one place that turns an exhausted budget or a crash into a
usage summary), `SourceRun` (a finished run copied into a new run directory), `rebuild` (QA, build and report on an already written spec)."""
from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from deckgen.config import CONFIG
from deckgen.ledger import BudgetAbort, Ledger, Prices
from deckgen.llm import RoleLLM, Tracer, make_llm
from deckgen.pipeline import Request, run_pipeline, slug
from deckgen.research.datasets import DataSets
from deckgen.research.facts import Sheet, build_sheet, odd_ones
from deckgen.research.relevance import filter_passages
from deckgen.research.tools import Budget, HttpFetcher, ResearchTools, TavilySearch
from deckgen.schemas import DeckSpec
from deckgen.schemas.research import Brief, Findings, Outline
from deckgen.spec.deck_checks import consistency
from deckgen.spec.repair import apply_instructions, repair_deck, write_log
from deckgen.spec.spec_generator import small_fix, sources_slide
from deckgen.spec.validate_spec import load_manifest

Echo = Callable[[str], None]
CARRIED_ARTIFACTS = ("brief.json", "findings.json", "outline.json", "facts.json", "usage.jsonl", "deck_notes.json")
CROSS_SLIDE_KINDS = ("contradiction", "unclear_reference", "title_mismatch", "redundancy")


def uses_anthropic() -> bool:
    return any(r.provider == "anthropic" for r in CONFIG.build_roles().values())


def make_ledger(run_dir: Path, budget: float | None, echo: Echo = print) -> Ledger:
    cap = CONFIG.budget_usd if budget is None else budget
    return Ledger(run_dir / "usage.jsonl", Prices.load(), cap, echo=echo)


def make_tools() -> ResearchTools:
    budget = Budget(CONFIG.max_tool_calls, CONFIG.max_pages, CONFIG.max_seconds, CONFIG.max_search_calls)
    return ResearchTools(TavilySearch(), HttpFetcher(), budget)


@contextmanager
def budget_guard(ledger: Ledger | None, out_dir: Path) -> Iterator[None]:
    """Always leave a usage summary behind: on success, on an exhausted budget (re-raised as BudgetAbort), and on any crash."""
    summary = out_dir / "usage_summary.md"
    try:
        yield
    except BudgetAbort:
        if ledger:
            ledger.write_summary(summary)
        raise
    except BaseException:
        if ledger:
            ledger.aborted = ledger.aborted or "run failed (see error); partial usage below"
            ledger.write_summary(summary)
        raise
    else:
        if ledger:
            ledger.write_summary(summary)


def _read(model, path: Path):
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@dataclass(frozen=True)
class SourceRun:
    """A finished run copied into a fresh run directory, with its stage artifacts loaded."""
    dst: Path
    brief: Brief
    outline: Outline
    findings: Findings
    spec: DeckSpec
    sheet: Sheet | None

    @classmethod
    def open(cls, run: str, out: str | None, suffix: str) -> SourceRun:
        src, dst = CONFIG.runs_dir / run, CONFIG.runs_dir / (out or f"{run}-{suffix}")
        dst.mkdir(parents=True, exist_ok=True)
        for name in CARRIED_ARTIFACTS:
            if (src / name).exists():
                shutil.copy(src / name, dst / name)
        facts = dst / "facts.json"
        return cls(dst=dst, brief=_read(Brief, dst / "brief.json"), outline=_read(Outline, dst / "outline.json"), findings=_read(Findings, dst / "findings.json"),
                   spec=_read(DeckSpec, src / "deck_spec.final.json"), sheet=_read(Sheet, facts) if facts.exists() else None)

    @property
    def brand_dir(self) -> Path:
        return CONFIG.brands_dir / self.brief.brand

    @property
    def usable(self) -> dict:
        return {f.id: f for f in self.findings.findings if not f.excluded}

    def request(self, tier_overrides: str | None) -> Request:
        return Request(self.brief.topic, self.brief.audience, self.brief.slide_count, self.brief.brand, tier_overrides=tier_overrides)


def rebuild(src: SourceRun, spec: DeckSpec, llm: RoleLLM, ledger: Ledger, tier_overrides: str | None) -> dict:
    """Write the repaired spec and run the pipeline's QA, build and report on it (earlier stages are reused from the run directory)."""
    (src.dst / "deck_spec.json").write_text(spec.model_dump_json(exclude_none=True, indent=1), encoding="utf-8")
    return run_pipeline(src.request(tier_overrides), llm, make_tools(), src.dst, auto_approve=True, do_qa=True, ledger=ledger)


def run_generate(*, topic: str, audience: str, slides: int, brand: str, run_id: str | None, from_stage: str | None, auto_approve: bool, qa_loop: bool,
                 no_cache: bool, tier_overrides: str | None, budget: float | None, echo: Echo) -> tuple[dict, Path]:
    """The staged pipeline (v1) on a topic. The cost ledger is on whenever an Anthropic role is configured or a budget was given."""
    run_dir = CONFIG.runs_dir / (run_id or f"{slug(topic)}-{time.strftime('%m%d-%H%M')}")
    run_dir.mkdir(parents=True, exist_ok=True)
    if no_cache:
        os.environ["DECKGEN_NO_CACHE"] = "1"
    ledger = make_ledger(run_dir, budget, echo) if (budget is not None or uses_anthropic()) else None
    llm = make_llm(Tracer(run_dir / "trace.jsonl"), ledger)
    if no_cache:
        llm.use_cache = False
    with budget_guard(ledger, run_dir):
        report = run_pipeline(Request(topic, audience, slides, brand, tier_overrides=tier_overrides), llm, make_tools(), run_dir,
                              auto_approve=auto_approve, from_stage=from_stage, do_qa=qa_loop, ledger=ledger)
    return report, run_dir


def run_repair(*, run: str, out: str | None, tier_overrides: str | None, budget: float | None, echo: Echo) -> tuple[dict, dict]:
    """Facts sheet, then up to two rounds of small-diff fixes for cross-slide problems, then QA, rebuild and report. Returns (report, repair log)."""
    src = SourceRun.open(run, out, "repaired")
    ledger = make_ledger(src.dst, budget, echo)
    llm = make_llm(Tracer(src.dst / "trace.jsonl"), ledger)
    with budget_guard(ledger, src.dst):
        sheet = src.sheet or _build_sheet(src, llm, ledger)
        ledger.stage = "repair"
        fixed, log = repair_deck(llm, src.spec, src.brief, src.outline, src.findings, src.brand_dir, sheet.text(), sheet, DataSets(src.findings.findings, sheet))
        ledger.stage_line("repair")
        write_log(src.dst / "repair_log.json", log)
        _merge_notes(src.dst, {"unresolved": log["unresolved"], "repair": {"before": log["before"], "after": log["after"]}})
        report = rebuild(src, fixed, llm, ledger, tier_overrides)
    return report, log


def _build_sheet(src: SourceRun, llm: RoleLLM, ledger: Ledger) -> Sheet:
    ledger.stage = "facts"
    sheet = build_sheet(llm, src.findings.findings)
    (src.dst / "facts.json").write_text(sheet.model_dump_json(indent=1), encoding="utf-8")
    ledger.stage_line("facts")
    return sheet


def _merge_notes(run_dir: Path, update: dict) -> None:
    path = run_dir / "deck_notes.json"
    _write_json(path, {**_load_json(path), **update})


def _passage_report(src: SourceRun, llm: RoleLLM, ledger: Ledger) -> dict:
    ledger.stage = "passage_filter"
    pf = filter_passages(llm, src.outline, src.findings)
    (src.dst / "passage_filter.json").write_text(pf.model_dump_json(indent=1), encoding="utf-8")
    ledger.stage_line("passage_filter")
    gone = set(pf.dropped_finding_ids)
    citing = {s.id: sorted(set(s.source_refs) & gone) for s in src.spec.slides if set(s.source_refs) & gone}
    return {"kept": sum(len(v) for v in pf.kept.values()), "dropped": len(pf.dropped), "findings_dropped": len(gone), "slides_citing_dropped_findings": citing}


def _sheet_fixes(src: SourceRun, slides: list, llm: RoleLLM, manifest: dict) -> list[dict]:
    """One small diff for each slide that quotes a different value than the facts sheet's wording."""
    if src.sheet is None:
        return []
    out = []
    for odd in odd_ones(slides, src.sheet):
        e = src.sheet.entries[odd["entry"]]
        message = f"this slide quotes '{odd['found']}' for {e.subject} / {e.attribute}, but the facts sheet wording is: {e.value}"
        out.append(small_fix(llm, slides, odd["slide"], src.usable, manifest, src.spec.title, src.spec.brand, message,
                             f"FACTS SHEET: {e.subject} / {e.attribute}: {e.value}. Use exactly this.", f"{e.subject} / {e.attribute}", "facts-sheet"))
    return out


def _cross_check(src: SourceRun, spec: DeckSpec, llm: RoleLLM, ledger: Ledger) -> tuple[list, dict]:
    ledger.stage = "cross_check"
    found = consistency(llm, spec, src.usable)
    counts = {kind: sum(p.kind == kind for p in found) for kind in CROSS_SLIDE_KINDS if any(p.kind == kind for p in found)}
    ledger.stage_line("cross_check")
    return found, {"found": counts, "contradictions_llm": counts.get("contradiction", 0)}


def run_fixpass(*, run: str, out: str | None, instructions: Path | None, tier_overrides: str | None, budget: float | None, echo: Echo) -> tuple[dict, dict]:
    """Passage relevance report, facts-sheet odd-one-out fixes, per-slide instructions, a rebuilt sources slide, ONE cross-slide check, then QA and report."""
    src = SourceRun.open(run, out, "fixed")
    ledger = make_ledger(src.dst, budget, echo)
    llm = make_llm(Tracer(src.dst / "trace.jsonl"), ledger)
    log: dict = {}
    with budget_guard(ledger, src.dst):
        log["passages"] = _passage_report(src, llm, ledger)
        slides, manifest = list(src.spec.slides), load_manifest(src.brand_dir)
        ledger.stage = "fix"
        log["facts_odd_ones"] = _sheet_fixes(src, slides, llm, manifest)
        if instructions:
            ctx = {"by_id": src.usable, "manifest": manifest, "findings": src.findings, "outline": src.outline, "brief": src.brief, "brand_dir": src.brand_dir,
                   "facts_text": src.sheet.text() if src.sheet else "", "data": DataSets(src.findings.findings, src.sheet), "deck_title": src.spec.title, "brand": src.spec.brand}
            slides, log["instructions"] = apply_instructions(llm, slides, _load_json(instructions), ctx)
        used = [src.usable[r] for s in slides[:-1] for r in s.source_refs if r in src.usable]
        slides[-1] = sources_slide(len(slides), used)       # rebuilt: de-duplicated, no stray characters
        ledger.stage_line("fix")
        fixed = src.spec.model_copy(update={"slides": slides})
        found, log["cross_check"] = _cross_check(src, fixed, llm, ledger)
        _merge_notes(src.dst, {"unresolved": [p.model_dump() for p in found], "cross_check": log["cross_check"], "fixpass": log})
        report = rebuild(src, fixed, llm, ledger, tier_overrides)
    return report, log
