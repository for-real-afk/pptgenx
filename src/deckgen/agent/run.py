"""Run the single-agent deck loop end to end: system prompt (skill file + brand style guide + layout names), phase budget, loop, a clean finish on every exit
(a budget stop, a limit, an agent that never calls finish: the best build is kept and what is left is reported), then run_report.md, usage_summary.md and
review_before_use.md."""
from __future__ import annotations

import os
import statistics
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from deckgen.agent.builder import BuildRecord
from deckgen.agent.loop import AgentLoop, anthropic_transport
from deckgen.agent.phases import PhaseBudget
from deckgen.agent.tools import AgentTools, tool_definitions
from deckgen.agent.workspace import ToolError
from deckgen.config import CONFIG
from deckgen.ledger import BudgetAbort, Ledger, Prices
from deckgen.qa.slide_text import SlideText, extract

NL = chr(10)
SKILL = CONFIG.root / "skills" / "deck_agent" / "SKILL.md"
HEDGE = ("approximately", "roughly", "reportedly", "according to", "sources differ", "estimated", "about", "around", "some sources")
PHASE_NAMES = ("research", "write", "look", "wrapup")


def system_prompt(brand_dir: Path) -> str:
    from deckgen.spec.spec_generator import _layout_menu

    style = (brand_dir / "style_guide.md").read_text(encoding="utf-8")
    return f"{SKILL.read_text(encoding='utf-8')}{NL}{NL}## Brand style guide{NL}{style}{NL}{NL}## Layout names and placeholders (the frame){NL}{_layout_menu(brand_dir)}"


def phase_budget(total: float) -> PhaseBudget:
    return PhaseBudget(total, CONFIG.agent_research_frac, CONFIG.agent_write_frac, CONFIG.agent_look_min_frac, CONFIG.agent_reserve_frac)


def estimate_agent(max_calls: int, prices: Prices, model: str, haiku: str) -> dict:
    """Assumption-based estimate (printed with its assumptions). The budget guard, not this number, is what bounds the spend."""
    p, h = prices.price(model), prices.price(haiku)
    turns, ctx, out = max_calls + 1, 60_000, 1_800     # tool calls + 1; average context tokens per turn; average output tokens per turn
    per_turn = (ctx * 0.80 * p.cache_read + ctx * 0.10 * p.cache_write + ctx * 0.10 * p.input + out * p.output) / 1e6
    haiku_cost = 20 * (3_000 * h.input + 400 * h.output) / 1e6
    return {"upper_usd": round(turns * per_turn + haiku_cost, 2), "typical_usd": round(0.45 * turns * per_turn + haiku_cost, 2), "turns": turns,
            "assumptions": f"{turns} model turns at about {ctx} context tokens (80% cache reads, 10% cache writes), {out} output tokens per turn, plus 20 Haiku reads"}


def _approval_hook(tools: AgentTools, yes: bool, run_dir: Path, echo: Callable[[str], None]):
    done = {"v": False}

    def hook() -> str | None:
        if done["v"] or tools.builder.script or not tools.work.exists("outline.md"):
            return None
        if yes:
            done["v"] = True
            (run_dir / "approval.log").write_text("outline auto-approved (--yes) at " + time.strftime("%Y-%m-%d %H:%M:%S") + NL, encoding="utf-8")
            echo("outline auto-approved (--yes)")
            return "The outline is approved (auto-approved). Continue: write the build script."
        import typer

        echo(tools.work.read("outline.md"))
        if typer.confirm("Approve this outline?", default=True):
            done["v"] = True
            (run_dir / "approval.log").write_text("outline approved by the user" + NL, encoding="utf-8")
            return "The outline is approved. Continue: write the build script."
        return "Change the outline as follows, then continue: " + typer.prompt("Your changes")

    return hook


def _salvage(tools: AgentTools) -> str:
    """The agent did not finish: keep the best build we have (building the script once if nothing was built yet). Returns what is left unresolved."""
    b = tools.builder
    if b.finished:
        return ""
    if not b.builds and b.script:
        try:
            b.wrapup = False
            b.t_run_build()
        except ToolError as e:
            return f"no deck could be built: {str(e)[:200]}"
    record: BuildRecord | None = b.best()
    if record is None:
        return "no deck was built"
    notes = tools.work.path("review.md")
    b.finalize(record, notes if notes.exists() else None)
    left = []
    if record.hard:
        left.append(f"{len(record.hard)} hard issues (overflow, off-slide or invalid file) remain: slides {sorted({i.slide for i in record.hard})}")
    unseen = sorted(b.needs_view(record) - set(b.views))
    if unseen:
        left.append(f"slides never looked at: {unseen}")
    return "; ".join(left) or "nothing blocking, but the agent did not call finish"


def _slide_stats(slides: list[SlideText]) -> tuple[dict, dict]:
    def hedge_words(s: SlideText) -> int:
        text = " ".join(s.texts).lower()
        return sum(text.count(w) for w in HEDGE)

    per = [hedge_words(s) for s in slides]
    bad = [s.number for s in slides if any(w in s.title.lower() for w in HEDGE) or any(w in " ".join(c for t in s.tables for row in t for c in row).lower() for w in HEDGE)]
    hedging = {"total_words": sum(per), "max_per_slide": max(per, default=0), "slides_over_cap_2": sum(n > 2 for n in per), "in_title_or_table": bad}
    mix = Counter(s.layout for s in slides)
    mix.update({"slides with a table": sum(bool(s.tables) for s in slides), "slides with a chart": sum(bool(s.charts) for s in slides)})
    return hedging, dict(mix)


def _context_sizes(ledger: Ledger) -> dict:
    sizes = [r["context_tokens"] for r in ledger.rows if r["role"] == "agent" and r.get("context_tokens")]
    return {"turns": len(sizes), "min": min(sizes), "median": int(statistics.median(sizes)), "max": max(sizes)} if sizes else {"turns": 0}


def report_md(r: dict) -> str:
    rows = [f"# Run report: {r['run_id']}", "", f"Topic: {r['topic']}", "", f"**PHOTOS: {r['photos']}**", "",
            f"- stop reason: {r['stop_reason']}" + (f"; left unresolved: {r['left']}" if r.get("left") else ""),
            f"- slides built: {r['slides_built']} of {r['slides_requested']} requested",
            f"- phases: {r['phase_log'] or 'no phase ended early'}; final phase {r['final_phase']}",
            f"- cost: ${r['total_usd']:.4f} of the ${r['budget_usd']:.2f} budget" + ("; **ABORTED by the budget guard**" if r.get("aborted") else ""),
            f"- cost by phase: {r['phase_cost']}; by model: {r['model_cost']}",
            f"- pages fetched: {r['pages']} (cap {r['page_cap']}); gaps named in notes.md: {len(r['gaps'])}: {r['gaps']}",
            f"- context tokens per turn: {r['context']}",
            f"- tool calls: {r['tool_calls']} ({r['tool_failures']} failed); model turns: {r['model_calls']}; builds: {r['builds']}; slide views: {r['slide_views']}; slides viewed: {r['viewed_slides']}; required but never viewed: {r['never_viewed'] or 'none'}",
            f"- provider prompt cache: {r['cache_hit_rate']:.1f}% of input tokens read from cache",
            f"- failed or cut-off model calls: {r['failed_calls']} costing ${r['failed_cost']:.4f}",
            f"- hedging: {r['hedging']}", f"- layout mix: {r['layout_mix']}", f"- review list: {r['review_items']} items in review_before_use.md", "", r["usage"]]
    return NL.join(rows)


def _finish_report(topic: str, slides: int, run_dir: Path, ledger: Ledger, tools: AgentTools, phases: PhaseBudget, stats: dict, left: str, aborted: str) -> dict:
    deck = run_dir / "deck.pptx"
    built = extract(deck) if deck.exists() else []
    hedging, mix = _slide_stats(built)
    model_rows = [r for r in ledger.rows if r["role"] != "tool" and r["cache"] != "hit"]
    tin, cr, cw = (sum(r[k] for r in model_rows) for k in ("input_tokens", "cache_read_tokens", "cache_write_tokens"))
    bad = [r for r in model_rows if r["outcome"] != "ok"]
    notes = run_dir / "review_before_use.md"
    by_model: Counter = Counter()
    for r in model_rows:
        by_model[r["model"]] += r["cost_usd"]
    report = {
        "run_id": run_dir.name, "topic": topic, "stop_reason": stats["stop_reason"], "left": left, "slides_built": len(built), "slides_requested": slides,
        "phase_log": phases.notes, "final_phase": phases.phase, "phase_cost": {p: round(ledger.spent(p), 4) for p in PHASE_NAMES}, "model_cost": {m: round(c, 4) for m, c in by_model.items()},
        "total_usd": ledger.total, "budget_usd": phases.total, "aborted": aborted, "pages": len(tools.reader.fetched), "page_cap": tools.reader.max_pages, "gaps": tools.work.gaps(),
        "context": _context_sizes(ledger), "tool_calls": stats["tool_calls"], "tool_failures": sum(1 for x in ledger.rows if x["role"] == "tool" and x["outcome"] != "ok"),
        "model_calls": stats["model_calls"], "builds": tools.builder.build_count, "slide_views": sum(tools.builder.views.values()),
        "viewed_slides": sorted(tools.builder.views), "never_viewed": sorted(tools.builder.needs_view(tools.builder.best()) - set(tools.builder.views)) if tools.builder.best() else [], "cache_hit_rate": 100 * cr / max(tin + cr + cw, 1),
        "failed_calls": len(bad), "failed_cost": sum(x["cost_usd"] for x in bad), "hedging": hedging, "layout_mix": mix,
        "review_items": sum(1 for line in notes.read_text(encoding="utf-8").splitlines() if line.strip().startswith(("-", "*"))) if notes.exists() else 0,
        "photos": "skipped: the agent loop has no photo tool in this version" + ("" if os.environ.get("DECKGEN_CONTACT") else "; DECKGEN_CONTACT is not set"),
        "seconds": stats["seconds"], "usage": ledger.summary_md(),
    }
    (run_dir / "run_report.md").write_text(report_md(report), encoding="utf-8")
    return report


def run_agent(topic: str, audience: str, slides: int, run_dir: Path, *, budget: float, yes: bool, brand: str = "default_v1", tier_overrides: str | None = None,
              transport=None, search=None, summarizer=None, inspector=None, renderer=None, page_loader=None, echo: Callable[[str], None] = print) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    brand_dir = CONFIG.brands_dir / brand
    roles = CONFIG.build_roles()
    model, haiku = roles["writer"].model, roles["extractor"].model
    ledger = Ledger(run_dir / "usage.jsonl", Prices.load(), budget, echo=echo)
    phases = phase_budget(budget)
    from deckgen.research.weigh import TierTable

    table = TierTable(CONFIG.root / "config" / "tier_overrides" / f"{tier_overrides}.json") if tier_overrides else TierTable()
    tools = AgentTools(run_dir, brand_dir, ledger, phases, haiku_model=haiku, max_slides=slides, max_pages=CONFIG.agent_max_pages, free_pages=CONFIG.agent_free_pages,
                       max_slide_rounds=CONFIG.agent_max_slide_rounds, tier_table=table, search=search, summarizer=summarizer, inspector=inspector, renderer=renderer, page_loader=page_loader)
    loop = AgentLoop(tools, ledger, phases, model=model, system_text=system_prompt(brand_dir), tool_defs=tool_definitions(), max_calls=CONFIG.agent_max_tool_calls,
                     max_seconds=CONFIG.agent_max_seconds, transport=transport or anthropic_transport, approval=_approval_hook(tools, yes, run_dir, echo))
    task = (f"Topic: {topic}{NL}Audience: {audience}{NL}Slides: at most {slides} in total, including the title slide, the closing slide and the sources slide.{NL}"
            f"Budget: ${budget:.2f} in total; research may use {CONFIG.agent_research_frac:.0%}, writing {CONFIG.agent_write_frac:.0%}, the rest is for looking and fixing.{NL}"
            + ("Approval: the outline is auto-approved; write outline.md, then carry on with the build script." if yes else "Approval: write outline.md, then stop and wait for the user's approval.")
            + NL + "Work from the skill file instructions. Begin with research.")
    return _execute(loop, tools, ledger, phases, run_dir, topic, slides, task)


def _execute(loop: AgentLoop, tools: AgentTools, ledger: Ledger, phases: PhaseBudget, run_dir: Path, topic: str, slides: int, task: str) -> dict:
    """Run the loop to its end, whatever ends it, and leave the best deck, the usage summary and the run report behind."""
    aborted = ""
    try:
        stats = loop.run(task)
    except BudgetAbort as e:
        aborted = str(e)
        stats = {"stop_reason": f"budget guard: {aborted}", "tool_calls": loop.tool_calls, "model_calls": loop.model_calls, "seconds": 0}
    except Exception as e:  # noqa: BLE001 - a crash must still leave the usage summary and the best deck
        stats = {"stop_reason": f"error: {type(e).__name__}: {str(e)[:200]}", "tool_calls": loop.tool_calls, "model_calls": loop.model_calls, "seconds": 0}
        ledger.aborted = ledger.aborted or stats["stop_reason"]
    left = _salvage(tools)
    if aborted:
        ledger.aborted = aborted
    ledger.write_summary(run_dir / "usage_summary.md")
    return _finish_report(topic, slides, run_dir, ledger, tools, phases, stats, left, aborted)
