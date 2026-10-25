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
    llm = make_llm(Tracer(run_dir / "trace.jsonl"))
    if no_cache:
        llm.use_cache = False
    report = run_pipeline(Request(topic, audience, slides, brand), llm, make_tools(), run_dir,
                          auto_approve=auto_approve, from_stage=from_stage, do_qa=qa_loop)
    typer.echo(report_md(report))
    typer.echo(f"\ndeck: {run_dir / 'deck.pptx'}\nimages: {run_dir / 'final' / 'images'}\nsources: {run_dir / 'sources.md'}")


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
