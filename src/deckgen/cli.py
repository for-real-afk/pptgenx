"""Command line: parses options, calls the workflows, prints. Orchestration lives in `workflows`, health checks in `doctor`."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import typer

from deckgen.config import CONFIG
from deckgen.ledger import BudgetAbort, Ledger

app = typer.Typer(no_args_is_help=True, add_completion=False)
brand_app = typer.Typer(no_args_is_help=True)
app.add_typer(brand_app, name="brand")

EXIT_BUDGET = 2
EXIT_ESTIMATE_TOO_HIGH = 3


def _abort_exit(e: BudgetAbort) -> typer.Exit:
    typer.echo(f"ABORTED BEFORE SPENDING OVER THE CAP: {e}")
    return typer.Exit(EXIT_BUDGET)


@app.command()
def estimate(slides: int = typer.Option(14), limit: float = typer.Option(10.0, help="ask before spending if the UPPER estimate exceeds this")) -> None:
    """Dry cost estimate from prices.yaml and the configured roles. Spends nothing."""
    from deckgen.estimate import estimate as run_estimate
    from deckgen.estimate import table
    from deckgen.ledger import Prices

    result = run_estimate(slides, CONFIG.build_roles(), Prices.load())
    typer.echo(table(result))
    if result["upper_usd"] > limit:
        typer.echo(f"STOP: upper estimate ${result['upper_usd']} > ${limit}. Ask before running.")
        raise typer.Exit(EXIT_ESTIMATE_TOO_HIGH)


@app.command()
def checks(profile: str = typer.Option(None, help="strict | demoted")) -> None:
    """Print the check registry: category and severity of every check."""
    from deckgen.registry import table

    typer.echo(table(profile))


@app.command()
def doctor() -> None:
    """Check render toolchain, Node renderer deps, fonts, models, prices, writable dirs."""
    from deckgen.doctor import run_doctor

    raise typer.Exit(0 if run_doctor(typer.echo) else 1)


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
    from deckgen.qa import policy
    from deckgen.qa.fix_loop import LLMFixer, run_qa
    from deckgen.schemas import DeckSpec

    run_dir = CONFIG.runs_dir / (run or "adhoc_qa")
    run_dir.mkdir(parents=True, exist_ok=True)
    deck = DeckSpec.model_validate_json((spec or run_dir / "deck_spec.json").read_text(encoding="utf-8"))
    tracer = Tracer(run_dir / "trace.jsonl")
    llm = make_llm(tracer)
    final, report = run_qa(deck, CONFIG.brands_dir / brand, run_dir, llm, LLMFixer(llm) if fix else None)
    (run_dir / "deck_spec.final.json").write_text(final.model_dump_json(exclude_none=True, indent=1), encoding="utf-8")
    report_file = run_dir / "qa_report.md"
    typer.echo(report_file.read_text(encoding="utf-8") if report_file.exists() else str(report))
    typer.echo(tracer.summary())
    raise typer.Exit(1 if report.render_error or report.file_errors or policy.blocking(report.remaining) else 0)


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
    """Topic -> researched, sourced, QA'd .pptx under runs/<run_id>/ (the staged pipeline, v1)."""
    from deckgen.pipeline import report_md
    from deckgen.workflows import run_generate

    try:
        report, run_dir = run_generate(topic=topic, audience=audience, slides=slides, brand=brand, run_id=run_id, from_stage=from_stage, auto_approve=auto_approve,
                                       qa_loop=qa_loop, no_cache=no_cache, tier_overrides=tier_overrides, budget=budget, echo=typer.echo)
    except BudgetAbort as e:
        raise _abort_exit(e) from e
    typer.echo(report_md(report))
    typer.echo(f"\ndeck: {run_dir / 'deck.pptx'}\nimages: {run_dir / 'final' / 'images'}\nsources: {run_dir / 'sources.md'}")


app.command("pipeline", help="The staged pipeline (v1): same as `generate`.")(generate)


@app.command()
def repair(run: str = typer.Option(..., help="finished run id under runs/ (needs deck_spec.final.json, findings.json, brief.json, outline.json)"),
           out: str = typer.Option(None, help="new run id for the repaired deck (default <run>-repaired)"),
           tier_overrides: str = typer.Option(None, help="same per-topic tier overrides as the original run"),
           budget: float = typer.Option(None, "--budget", help="hard USD cap (counts the copied usage of the source run too)")) -> None:
    """Repair pass on a finished deck: facts sheet, up to 2 rounds of small-diff fixes for cross-slide problems, then QA, rebuild and report."""
    from deckgen.pipeline import report_md
    from deckgen.workflows import run_repair

    try:
        report, log = run_repair(run=run, out=out, tier_overrides=tier_overrides, budget=budget, echo=typer.echo)
    except BudgetAbort as e:
        raise _abort_exit(e) from e
    typer.echo(f"contradictions before {log['before'].get('contradiction', 0)} -> after {log['after'].get('contradiction', 0)}; all kinds {log['before']} -> {log['after']}")
    typer.echo(report_md(report))


@app.command()
def fixpass(run: str = typer.Option(..., help="finished run id under runs/"), out: str = typer.Option(None, help="new run id (default <run>-fixed)"),
            instructions: Path = typer.Option(None, help="JSON {slide number: instruction | {text, rewrite}} applied as small diffs / rewrites"),
            tier_overrides: str = typer.Option(None), budget: float = typer.Option(None, "--budget")) -> None:
    """Targeted fix pass on a finished deck: passage report, facts-sheet fixes, your per-slide instructions, ONE cross-slide check, then QA and report."""
    from deckgen.pipeline import report_md
    from deckgen.workflows import run_fixpass

    try:
        report, log = run_fixpass(run=run, out=out, instructions=instructions, tier_overrides=tier_overrides, budget=budget, echo=typer.echo)
    except BudgetAbort as e:
        raise _abort_exit(e) from e
    typer.echo(json.dumps(log, indent=1, ensure_ascii=False)[:3000])
    typer.echo(report_md(report))


@app.command()
def agent(topic: str = typer.Option(..., help="deck topic"), audience: str = typer.Option("general audience"),
          slides: int = typer.Option(14, help="maximum slides, incl. title, closing, sources"), brand: str = typer.Option("default_v1"), run_id: str = typer.Option(None),
          yes: bool = typer.Option(False, "--yes", help="auto-approve the outline (logged)"),
          budget: float = typer.Option(None, "--budget", help="hard USD cap for this run (default: config agent_budget_usd)"),
          tier_overrides: str = typer.Option(None), estimate_only: bool = typer.Option(False, "--estimate", help="print the cost estimate and stop")) -> None:
    """Single-agent loop (v2): research, write the whole deck, build, LOOK at every slide, fix, hand over."""
    from deckgen.agent.run import estimate_agent, report_md, run_agent
    from deckgen.ledger import Prices
    from deckgen.pipeline import slug

    cap = CONFIG.agent_budget_usd if budget is None else budget
    roles = CONFIG.build_roles()
    est = estimate_agent(CONFIG.agent_max_tool_calls, Prices.load(), roles["writer"].model, roles["extractor"].model)
    typer.echo(f"estimate: upper ${est['upper_usd']}, typical ${est['typical_usd']} (budget ${cap}); assumptions: {est['assumptions']}")
    if est["upper_usd"] > cap:
        typer.echo("STOP: the upper estimate is over the budget. Raise --budget, lower DECKGEN_AGENT_MAX_TOOL_CALLS, or confirm you accept the budget guard stopping the run.")
        if not estimate_only and not typer.confirm("Run anyway (the guard still stops at the budget)?", default=False):
            raise typer.Exit(EXIT_ESTIMATE_TOO_HIGH)
    spent = Ledger.account_spent(CONFIG.runs_dir)
    left = CONFIG.agent_account_cap_usd - spent
    typer.echo(f"account: ${spent:.2f} spent across all runs of ${CONFIG.agent_account_cap_usd:.2f}; ${left:.2f} left")
    if left < cap:
        typer.echo(f"STOP: this run's budget (${cap}) is more than the ${left:.2f} left under the account cap. Lower --budget or raise DECKGEN_ACCOUNT_CAP_USD on purpose.")
        if not estimate_only and not typer.confirm("Run anyway?", default=False):
            raise typer.Exit(EXIT_ESTIMATE_TOO_HIGH)
    if estimate_only:
        raise typer.Exit(0)
    run_dir = CONFIG.runs_dir / (run_id or f"{slug(topic)}-agent-{time.strftime('%m%d-%H%M')}")
    result = run_agent(topic, audience, slides, run_dir, budget=cap, yes=yes, brand=brand, tier_overrides=tier_overrides, echo=typer.echo)
    typer.echo(report_md(result))
    typer.echo(f"deck: {run_dir / 'deck.pptx'}")


@app.command("agent-fix")
def agent_fix(run: str = typer.Option(..., help="finished agent run id under runs/ (needs deck.pptx and work/build.js)"), out: str = typer.Option(None, help="new run id (default <run>-fix)"),
              instructions: Path = typer.Option(..., help="text file with the fixes to make"),
              slides: str = typer.Option(..., help="comma-separated slide numbers whose current text is shown to the agent, e.g. 3,10,11,12,13"),
              budget: float = typer.Option(0.6, "--budget", help="hard USD cap for this pass"), tier_overrides: str = typer.Option(None)) -> None:
    """Bounded fix pass on a finished agent run (no research, starts in look-and-fix); writes a new run directory."""
    from deckgen.agent.fixup import run_fixup
    from deckgen.agent.run import report_md

    spent = Ledger.account_spent(CONFIG.runs_dir)
    left = CONFIG.agent_account_cap_usd - spent
    typer.echo(f"account: ${spent:.2f} spent of ${CONFIG.agent_account_cap_usd:.2f}; ${left:.2f} left; this pass is capped at ${budget:.2f}")
    if left < budget:
        typer.echo("STOP: the pass budget is more than what is left under the account cap.")
        raise typer.Exit(EXIT_ESTIMATE_TOO_HIGH)
    out_dir = CONFIG.runs_dir / (out or f"{run}-fix")
    result = run_fixup(CONFIG.runs_dir / run, out_dir, instructions.read_text(encoding="utf-8"), [int(n) for n in slides.split(",")], budget=budget,
                       tier_overrides=tier_overrides, echo=typer.echo)
    typer.echo(report_md(result))
    typer.echo(f"deck: {out_dir / 'deck.pptx'}")


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
    from deckgen.experiment import Variant, default_variants, run_experiment
    from deckgen.llm import RoleLLM, Tracer, parse_role
    from deckgen.pipeline import Request
    from deckgen.schemas.research import Brief

    base = CONFIG.runs_dir / base_run
    brief = Brief.model_validate_json((base / "brief.json").read_text(encoding="utf-8"))
    req = Request(topic or brief.topic, brief.audience, brief.slide_count, brief.brand)
    variants = default_variants(strong, cheap) + ([Variant("G", baseline, "demoted", "baseline writer, gates demoted")] if baseline else [])

    def make(var, run_dir):
        roles = CONFIG.build_roles()
        if var.writer:
            roles["writer"] = parse_role(var.writer, temperature=roles["writer"].temperature, max_tokens=roles["writer"].max_tokens, seed=7)
        return RoleLLM(roles, Tracer(run_dir / "trace.jsonl"), use_cache=False)  # no cache: repeated runs must be real samples

    out = CONFIG.runs_dir / "experiments" / f"{base_run}-{time.strftime('%m%d-%H%M')}"
    run_experiment(base, req, variants, runs, make, out, do_qa=qa)
    typer.echo((out / "summary.md").read_text(encoding="utf-8"))
    typer.echo(f"\nsummary: {out / 'summary.md'}")


if __name__ == "__main__":
    app()
