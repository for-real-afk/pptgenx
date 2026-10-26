from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import typer

from deckgen.config import CONFIG

app = typer.Typer(no_args_is_help=True, add_completion=False)
brand_app = typer.Typer(no_args_is_help=True)
app.add_typer(brand_app, name="brand")


def _check(name: str, ok: bool, detail: str = "") -> bool:
    typer.echo(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


def _check_models() -> list[bool]:
    """Fetch each configured provider's live model list and warn when a role points at a model that is not there."""
    from deckgen.providers import get_adapter

    out, seen = [], {}
    for role, spec in CONFIG.build_roles().items():
        for provider, model in [(spec.provider, spec.model), *spec.fallbacks]:
            try:
                if provider not in seen:
                    seen[provider] = set(get_adapter(provider).list_models())
                ok = model in seen[provider]
                out.append(_check(f"role {role}: {provider}:{model}", ok, "" if ok else "model not in the provider's model list"))
            except Exception as e:  # noqa: BLE001 - missing key or network: report, do not crash doctor
                out.append(_check(f"role {role}: {provider}:{model}", False, f"cannot list models ({type(e).__name__}: {str(e)[:80]})"))
    return out


def _check_costs() -> list[bool]:
    """Anthropic roles: key present (never printed) and a price for every model (the ledger never guesses)."""
    import os

    from deckgen.ledger import Prices

    models = sorted({r.model for r in CONFIG.build_roles().values() if r.provider == "anthropic"})
    if not models:
        return []
    out = [_check("ANTHROPIC_API_KEY set", bool(os.environ.get("ANTHROPIC_API_KEY")), "add it to .env")]
    missing = Prices.load().missing(models)
    out.append(_check("prices.yaml complete for " + ", ".join(models), not missing, "; ".join(missing)))
    return out


@app.command()
def estimate(slides: int = typer.Option(14), limit: float = typer.Option(10.0, help="ask before spending if the UPPER estimate exceeds this")) -> None:
    """Dry cost estimate from prices.yaml and the configured roles. Spends nothing."""
    from deckgen.estimate import estimate as est, table
    from deckgen.ledger import Prices

    e = est(slides, CONFIG.build_roles(), Prices.load())
    typer.echo(table(e))
    if e["upper_usd"] > limit:
        typer.echo(f"STOP: upper estimate ${e['upper_usd']} > ${limit}. Ask before running.")
        raise typer.Exit(3)


@app.command()
def checks(profile: str = typer.Option(None, help="strict | demoted")) -> None:
    """Print the check registry: category and severity of every check."""
    from deckgen.registry import table

    typer.echo(table(profile))


@app.command()
def doctor() -> None:
    """Check render toolchain, Node renderer deps, fonts, writable dirs."""
    from deckgen.qa import soffice

    results = []
    node = shutil.which("node")
    results.append(_check("node", bool(node), subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip() if node else "not found"))
    results.append(_check("pptxgenjs installed", (CONFIG.root / "node_modules" / "pptxgenjs").exists(), "run `npm install`"))
    try:
        mode = soffice.backend()
        results.append(_check("render backend (soffice + pdftoppm)", True, mode))
        fonts = soffice.list_fonts()
        for need in ("Carlito", "Caladea", "Liberation"):
            results.append(_check(f"font {need}", need.lower() in fonts.lower()))
    except soffice.RenderError as e:
        results.append(_check("render backend", False, str(e)))
    results += _check_models()
    results += _check_costs()
    for d in (Path(tempfile.gettempdir()), CONFIG.runs_dir):
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".deckgen_probe"
        try:
            probe.write_text("x"); probe.unlink(); results.append(_check(f"writable {d}", True))
        except OSError as e:
            results.append(_check(f"writable {d}", False, str(e)))
    raise typer.Exit(0 if all(results) else 1)


@brand_app.command("build")
def brand_build(brand: Path = typer.Option(..., help="brand dir, e.g. brand/default_v1")) -> None:
    """layouts.js -> manifest.json."""
    r = subprocess.run(["node", str(CONFIG.renderer_dir / "gen_manifest.js"), str(brand.resolve())], capture_output=True, text=True)
    typer.echo(r.stdout + r.stderr)
    raise typer.Exit(r.returncode)


@app.command()
def qa(run: str = typer.Option(None, help="run id under runs/ containing deck_spec.json"),
       spec: Path = typer.Option(None, help="path to a DeckSpec json instead of a run"),
       brand: str = typer.Option("default_v1"), fix: bool = typer.Option(True, help="apply LLM fixes between rounds")) -> None:
    """Run file/content/automated/visual QA (+ fix loop) on a deck spec."""
    from deckgen.llm import Tracer, make_llm
    from deckgen.qa.fix_loop import LLMFixer, run_qa
    from deckgen.schemas import DeckSpec

    run_dir = CONFIG.runs_dir / (run or "adhoc_qa")
    run_dir.mkdir(parents=True, exist_ok=True)
    src = spec or run_dir / "deck_spec.json"
    ds = DeckSpec.model_validate_json(src.read_text(encoding="utf-8"))
    tracer = Tracer(run_dir / "trace.jsonl")
    llm = make_llm(tracer)
    final, report = run_qa(ds, CONFIG.brands_dir / brand, run_dir, llm, LLMFixer(llm) if fix else None)
    (run_dir / "deck_spec.final.json").write_text(final.model_dump_json(exclude_none=True, indent=1), encoding="utf-8")
    typer.echo((run_dir / "qa_report.md").read_text(encoding="utf-8") if (run_dir / "qa_report.md").exists() else str(report))
    typer.echo(tracer.summary())
    from deckgen.qa import policy

    raise typer.Exit(1 if report.render_error or report.file_errors or policy.blocking(report.remaining) else 0)


def _uses_anthropic() -> bool:
    return any(r.provider == "anthropic" for r in CONFIG.build_roles().values())


def make_ledger(run_dir: Path, budget: float | None):
    from deckgen.ledger import Ledger, Prices

    return Ledger(run_dir / "usage.jsonl", Prices.load(), CONFIG.budget_usd if budget is None else budget, echo=typer.echo)


def make_tools() -> "ResearchTools":
    from deckgen.research.tools import Budget, HttpFetcher, ResearchTools, TavilySearch

    budget = Budget(CONFIG.max_tool_calls, CONFIG.max_pages, CONFIG.max_seconds, CONFIG.max_search_calls)
    return ResearchTools(TavilySearch(), HttpFetcher(), budget)


@app.command()
def generate(
    topic: str = typer.Option(..., help="deck topic"),
    audience: str = typer.Option("general audience"),
    slides: int = typer.Option(14),
    brand: str = typer.Option("default_v1"),
    run_id: str = typer.Option(None, help="re-use a run id to resume (completed stages are skipped)"),
    from_stage: str = typer.Option(None, help="force re-run from: plan|research|outline|spec|qa"),
    auto_approve: bool = typer.Option(False, help="skip the outline approval prompt"),
    qa_loop: bool = typer.Option(True, "--qa/--no-qa"),
    no_cache: bool = typer.Option(False, "--no-cache", help="ignore and do not write the LLM response cache"),
    tier_overrides: str = typer.Option(None, help="per-topic source tier overrides, e.g. wrc-group-b (config/tier_overrides/<name>.json)"),
    budget: float = typer.Option(None, "--budget", help="hard USD cap for the whole run (default: config budget_usd). Enables the cost ledger."),
) -> None:
    """Topic -> researched, sourced, QA'd .pptx under runs/<run_id>/."""
    import time

    from deckgen.llm import Tracer, make_llm
    from deckgen.pipeline import Request, report_md, run_pipeline, slug

    rid = run_id or f"{slug(topic)}-{time.strftime('%m%d-%H%M')}"
    run_dir = CONFIG.runs_dir / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    if no_cache:
        import os

        os.environ["DECKGEN_NO_CACHE"] = "1"  # read by Config at import; also pass use_cache explicitly below
    ledger = make_ledger(run_dir, budget) if (budget is not None or _uses_anthropic()) else None
    llm = make_llm(Tracer(run_dir / "trace.jsonl"), ledger)
    if no_cache:
        llm.use_cache = False
    from deckgen.ledger import BudgetAbort

    try:
        report = run_pipeline(Request(topic, audience, slides, brand, tier_overrides=tier_overrides), llm, make_tools(), run_dir,
                              auto_approve=auto_approve, from_stage=from_stage, do_qa=qa_loop, ledger=ledger)
    except BudgetAbort as e:
        ledger.write_summary(run_dir / "usage_summary.md")
        typer.echo(f"ABORTED BEFORE SPENDING OVER THE CAP: {e}")
        typer.echo(f"see {run_dir / 'usage_summary.md'}")
        raise typer.Exit(2)
    except BaseException:
        if ledger:
            ledger.aborted = ledger.aborted or "run failed (see error); partial usage below"
            ledger.write_summary(run_dir / "usage_summary.md")
        raise
    if ledger:
        ledger.write_summary(run_dir / "usage_summary.md")
    typer.echo(report_md(report))
    typer.echo(f"\ndeck: {run_dir / 'deck.pptx'}\nimages: {run_dir / 'final' / 'images'}\nsources: {run_dir / 'sources.md'}")


@app.command()
def repair(run: str = typer.Option(..., help="finished run id under runs/ (needs deck_spec.final.json, findings.json, brief.json, outline.json)"),
           out: str = typer.Option(None, help="new run id for the repaired deck (default <run>-repaired)"),
           tier_overrides: str = typer.Option(None, help="same per-topic tier overrides as the original run"),
           budget: float = typer.Option(None, "--budget", help="hard USD cap (counts the copied usage of the source run too)")) -> None:
    """Repair pass on a finished deck: facts sheet, then up to 2 rounds of small-diff fixes for contradictions, unexplained references and
    title mismatches (cross-slide checker re-run each round), then QA, rebuild and report in a new run dir."""
    import json
    import shutil

    from deckgen.ledger import BudgetAbort
    from deckgen.llm import Tracer, make_llm
    from deckgen.pipeline import Request, report_md, run_pipeline
    from deckgen.research.datasets import DataSets
    from deckgen.research.facts import Sheet, build_sheet
    from deckgen.schemas import DeckSpec
    from deckgen.schemas.research import Brief, Findings, Outline
    from deckgen.spec.repair import repair_deck, write_log

    src, dst = CONFIG.runs_dir / run, CONFIG.runs_dir / (out or f"{run}-repaired")
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("brief.json", "findings.json", "outline.json", "facts.json", "usage.jsonl", "deck_notes.json"):
        if (src / name).exists():
            shutil.copy(src / name, dst / name)
    brief = Brief.model_validate_json((dst / "brief.json").read_text(encoding="utf-8"))
    outline = Outline.model_validate_json((dst / "outline.json").read_text(encoding="utf-8"))
    findings = Findings.model_validate_json((dst / "findings.json").read_text(encoding="utf-8"))
    spec = DeckSpec.model_validate_json((src / "deck_spec.final.json").read_text(encoding="utf-8"))
    ledger = make_ledger(dst, budget)
    llm = make_llm(Tracer(dst / "trace.jsonl"), ledger)
    try:
        if not (dst / "facts.json").exists():
            ledger.stage = "facts"
            (dst / "facts.json").write_text(build_sheet(llm, findings.findings).model_dump_json(indent=1), encoding="utf-8")
            ledger.stage_line("facts")
        sheet = Sheet.model_validate_json((dst / "facts.json").read_text(encoding="utf-8"))
        ledger.stage = "repair"
        fixed, rep = repair_deck(llm, spec, brief, outline, findings, CONFIG.brands_dir / brief.brand, sheet.text(), sheet, DataSets(findings.findings, sheet))
        ledger.stage_line("repair")
        write_log(dst / "repair_log.json", rep)
        (dst / "deck_spec.json").write_text(fixed.model_dump_json(exclude_none=True, indent=1), encoding="utf-8")
        notes_path = dst / "deck_notes.json"
        notes = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.exists() else {}
        notes.update({"unresolved": rep["unresolved"], "repair": {"before": rep["before"], "after": rep["after"]}})
        notes_path.write_text(json.dumps(notes, indent=1), encoding="utf-8")
        report = run_pipeline(Request(brief.topic, brief.audience, brief.slide_count, brief.brand, tier_overrides=tier_overrides), llm, make_tools(), dst,
                              auto_approve=True, do_qa=True, ledger=ledger)
    except BudgetAbort as e:
        ledger.write_summary(dst / "usage_summary.md")
        typer.echo(f"ABORTED BEFORE SPENDING OVER THE CAP: {e}")
        raise typer.Exit(2)
    ledger.write_summary(dst / "usage_summary.md")
    typer.echo(f"contradictions before {rep['before'].get('contradiction', 0)} -> after {rep['after'].get('contradiction', 0)}; all kinds {rep['before']} -> {rep['after']}")
    typer.echo(report_md(report))


@app.command()
def fixpass(run: str = typer.Option(..., help="finished run id under runs/"), out: str = typer.Option(None, help="new run id (default <run>-fixed)"),
            instructions: Path = typer.Option(None, help="JSON {slide number: instruction | {text, rewrite}} applied as small diffs / rewrites"),
            tier_overrides: str = typer.Option(None), budget: float = typer.Option(None, "--budget")) -> None:
    """Targeted fix pass on a finished deck: passage relevance report, facts-sheet odd-one-out fixes, your per-slide instructions, a rebuilt sources
    slide, ONE cross-slide LLM check, then QA, rebuild and report in a new run dir."""
    import json
    import shutil

    from deckgen.ledger import BudgetAbort
    from deckgen.llm import Tracer, make_llm
    from deckgen.pipeline import Request, report_md, run_pipeline
    from deckgen.research.datasets import DataSets
    from deckgen.research.facts import Sheet, odd_ones
    from deckgen.research.relevance import filter_passages
    from deckgen.schemas import DeckSpec
    from deckgen.schemas.research import Brief, Findings, Outline
    from deckgen.spec.deck_checks import consistency
    from deckgen.spec.repair import apply_instructions
    from deckgen.spec.spec_generator import _small_fix, sources_slide
    from deckgen.spec.validate_spec import load_manifest

    src, dst = CONFIG.runs_dir / run, CONFIG.runs_dir / (out or f"{run}-fixed")
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("brief.json", "findings.json", "outline.json", "facts.json", "usage.jsonl", "deck_notes.json"):
        if (src / name).exists():
            shutil.copy(src / name, dst / name)
    brief = Brief.model_validate_json((dst / "brief.json").read_text(encoding="utf-8"))
    outline = Outline.model_validate_json((dst / "outline.json").read_text(encoding="utf-8"))
    findings = Findings.model_validate_json((dst / "findings.json").read_text(encoding="utf-8"))
    sheet = Sheet.model_validate_json((dst / "facts.json").read_text(encoding="utf-8")) if (dst / "facts.json").exists() else None
    spec = DeckSpec.model_validate_json((src / "deck_spec.final.json").read_text(encoding="utf-8"))
    by_id = {f.id: f for f in findings.findings if not f.excluded}
    brand_dir = CONFIG.brands_dir / brief.brand
    ledger = make_ledger(dst, budget)
    llm = make_llm(Tracer(dst / "trace.jsonl"), ledger)
    notes_path = dst / "deck_notes.json"
    notes = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.exists() else {}
    log: dict = {}
    try:
        ledger.stage = "passage_filter"
        pf = filter_passages(llm, outline, findings)
        (dst / "passage_filter.json").write_text(pf.model_dump_json(indent=1), encoding="utf-8")
        ledger.stage_line("passage_filter")
        gone = set(pf.dropped_finding_ids)
        log["passages"] = {"kept": sum(len(v) for v in pf.kept.values()), "dropped": len(pf.dropped), "findings_dropped": len(gone),
                           "slides_citing_dropped_findings": {s.id: sorted(set(s.source_refs) & gone) for s in spec.slides if set(s.source_refs) & gone}}
        slides = list(spec.slides)
        manifest = load_manifest(brand_dir)
        ledger.stage = "fix"
        log["facts_odd_ones"] = []
        if sheet is not None:
            for o in odd_ones(slides, sheet):
                e = sheet.entries[o["entry"]]
                log["facts_odd_ones"].append(_small_fix(llm, slides, o["slide"], by_id, manifest, spec.title, spec.brand,
                                                        f"this slide quotes '{o['found']}' for {e.subject} / {e.attribute}, but the facts sheet wording is: {e.value}",
                                                        f"FACTS SHEET: {e.subject} / {e.attribute}: {e.value}. Use exactly this.", f"{e.subject} / {e.attribute}", "facts-sheet"))
        ctx = {"by_id": by_id, "manifest": manifest, "findings": findings, "outline": outline, "brief": brief, "brand_dir": brand_dir,
               "facts_text": sheet.text() if sheet else "", "data": DataSets(findings.findings, sheet), "deck_title": spec.title, "brand": spec.brand}
        if instructions:
            slides, log["instructions"] = apply_instructions(llm, slides, json.loads(instructions.read_text(encoding="utf-8")), ctx)
        used = [by_id[r] for sl in slides[:-1] for r in sl.source_refs if r in by_id]
        slides[-1] = sources_slide(len(slides), used)       # rebuilt: de-duplicated, no stray characters
        ledger.stage_line("fix")
        fixed = spec.model_copy(update={"slides": slides})
        ledger.stage = "cross_check"
        found = consistency(llm, fixed, by_id)
        counts: dict[str, int] = {}
        for p in found:
            counts[p.kind] = counts.get(p.kind, 0) + 1
        log["cross_check"] = {"found": counts, "contradictions_llm": counts.get("contradiction", 0)}
        ledger.stage_line("cross_check")
        notes.update({"unresolved": [p.model_dump() for p in found], "cross_check": log["cross_check"], "fixpass": log})
        notes_path.write_text(json.dumps(notes, indent=1), encoding="utf-8")
        (dst / "deck_spec.json").write_text(fixed.model_dump_json(exclude_none=True, indent=1), encoding="utf-8")
        report = run_pipeline(Request(brief.topic, brief.audience, brief.slide_count, brief.brand, tier_overrides=tier_overrides), llm, make_tools(), dst,
                              auto_approve=True, do_qa=True, ledger=ledger)
    except BudgetAbort as e:
        ledger.write_summary(dst / "usage_summary.md")
        typer.echo(f"ABORTED BEFORE SPENDING OVER THE CAP: {e}")
        raise typer.Exit(2)
    ledger.write_summary(dst / "usage_summary.md")
    typer.echo(json.dumps(log, indent=1, ensure_ascii=False)[:3000])
    typer.echo(report_md(report))


@app.command("eval")
def eval_(golden: Path = typer.Option(Path("evals/golden_topics.json")), only: str = typer.Option(None, help="substring of topic"),
          runs: int = typer.Option(3, help="runs per topic (run 1 does the research)")) -> None:
    """Run the golden topics and print the per-run metrics table."""
    from evals.run_evals import run_all

    raise typer.Exit(run_all(golden, only, runs))


@app.command("experiment")
def experiment_cmd(
    base_run: str = typer.Option(..., help="run id under runs/ that holds brief.json + findings.json (the cached research)"),
    topic: str = typer.Option(None, help="defaults to the base run's brief"),
    runs: int = typer.Option(3, help="runs per variant"),
    strong: str = typer.Option("groq:openai/gpt-oss-120b", help="provider:model for the strong writer"),
    cheap: str = typer.Option("groq:llama-3.3-70b-versatile", help="provider:model for the cheap writer"),
    baseline: str = typer.Option("", help="optional extra variant G: provider:model (for example the Gemini baseline), gates demoted"),
    qa: bool = typer.Option(False, "--qa/--no-qa", help="also render and QA each run (slower; needs Docker and the critic role)"),
) -> None:
    """Variants A/B/C (strong+strict, cheap+demoted, strong+demoted) over the SAME cached findings; writes runs/experiments/<id>/summary.md."""
    import time

    from deckgen.experiment import Variant, default_variants, run_experiment
    from deckgen.llm import Tracer, RoleLLM, parse_role
    from deckgen.pipeline import Request
    from deckgen.schemas.research import Brief

    base = CONFIG.runs_dir / base_run
    brief = Brief.model_validate_json((base / "brief.json").read_text(encoding="utf-8"))
    req = Request(topic or brief.topic, brief.audience, brief.slide_count, brief.brand)
    variants = default_variants(strong, cheap) + ([Variant("G", baseline, "demoted", "baseline writer, gates demoted")] if baseline else [])

    def make(var, rd):
        roles = CONFIG.build_roles()
        if var.writer:
            roles["writer"] = parse_role(var.writer, temperature=roles["writer"].temperature, max_tokens=roles["writer"].max_tokens, seed=7)
        return RoleLLM(roles, Tracer(rd / "trace.jsonl"), use_cache=False)  # no cache: repeated runs must be real samples

    out = CONFIG.runs_dir / "experiments" / f"{base_run}-{time.strftime('%m%d-%H%M')}"
    run_experiment(base, req, variants, runs, make, out, do_qa=qa)
    typer.echo((out / "summary.md").read_text(encoding="utf-8"))
    typer.echo(chr(10) + f"summary: {out / 'summary.md'}")


if __name__ == "__main__":
    app()
