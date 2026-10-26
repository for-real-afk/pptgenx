"""Run the single-agent deck loop end to end: build the system prompt (skill file + layout manifest + limits + style guide), run the loop under the
cost budget, finish cleanly on every exit (a budget stop, a limit, an agent that never calls finish: the last build is kept and what is left is
reported), and write run_report.md, usage_summary.md and review_before_use.md."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from deckgen.agent.loop import AgentLoop, anthropic_transport
from deckgen.agent.tools import AgentTools, tool_definitions
from deckgen.config import CONFIG
from deckgen.ledger import BudgetAbort, Ledger, Prices
from deckgen.schemas import DeckSpec

NL = chr(10)
SKILL = CONFIG.root / "skills" / "deck_agent" / "SKILL.md"


def system_prompt(brand_dir: Path) -> str:
    from deckgen.spec.spec_generator import _layout_menu, _limits_menu

    style = (brand_dir / "style_guide.md").read_text(encoding="utf-8")
    return (SKILL.read_text(encoding="utf-8") + NL + NL + "## Brand style guide" + NL + style + NL + NL + "## Layouts (names, placeholders, character limits)" + NL
            + _layout_menu(brand_dir) + NL + NL + "## Field limits" + NL + _limits_menu())


def estimate_agent(max_calls: int, prices: Prices, model: str, haiku: str) -> dict:
    """Assumption-based estimate (printed with its assumptions). The budget guard, not this number, is what bounds the spend."""
    p, h = prices.price(model), prices.price(haiku)
    turns, ctx, out = max_calls + 1, 70_000, 1_800     # tool calls + 1; average context tokens per turn; average output tokens per turn
    per_turn = (ctx * 0.80 * p.cache_read + ctx * 0.10 * p.cache_write + ctx * 0.10 * p.input + out * p.output) / 1e6
    haiku_cost = 20 * (3_000 * h.input + 400 * h.output) / 1e6
    return {"upper_usd": round(turns * per_turn + haiku_cost, 2), "typical_usd": round(0.45 * turns * per_turn + haiku_cost, 2), "turns": turns,
            "assumptions": f"{turns} model turns at about {ctx} context tokens (80% cache reads, 10% cache writes), {out} output tokens per turn, plus 20 Haiku reads"}


def _approval_hook(tools: AgentTools, yes: bool, run_dir: Path, echo):
    done = {"v": False}

    def hook() -> str | None:
        outline = tools.work / "outline.md"
        if done["v"] or tools.build_count or not outline.exists():
            return None
        if yes:
            done["v"] = True
            (run_dir / "approval.log").write_text("outline auto-approved (--yes) at " + time.strftime("%Y-%m-%d %H:%M:%S") + NL, encoding="utf-8")
            echo("outline auto-approved (--yes)")
            return "The outline is approved (auto-approved). Continue with the full spec."
        import typer

        echo(outline.read_text(encoding="utf-8"))
        if typer.confirm("Approve this outline?", default=True):
            done["v"] = True
            (run_dir / "approval.log").write_text("outline approved by the user" + NL, encoding="utf-8")
            return "The outline is approved. Continue with the full spec."
        return "Change the outline as follows, then continue: " + typer.prompt("Your changes")

    return hook


def _fallback(tools: AgentTools) -> str:
    """The agent did not finish: keep the best deck we have. Returns what is left."""
    if tools.finished:
        return ""
    notes = tools.work / "review.md"
    try:
        if tools.last_spec_path is not None and not tools.builds:
            tools.t_build_deck(tools.last_spec_path.name)
    except Exception as e:  # noqa: BLE001
        return f"no deck could be built: {str(e)[:200]}"
    if not tools.builds:
        return "no deck was built"
    deck = Path(list(tools.builds)[-1])
    rec = tools.builds[str(deck)]
    tools.finalize(deck, notes if notes.exists() else None)
    left = []
    if rec["blocking"]:
        left.append(f"{rec['blocking']} blocking automated issues on slides {sorted(rec['per_slide'])}")
    if not rec["valid"]:
        left.append("the final spec was never validated with validate_spec")
    return "; ".join(left) or "nothing blocking, but the agent did not call finish"


def report_md(r: dict) -> str:
    rows = [f"# Run report: {r['run_id']}", "", f"Topic: {r['topic']}", "", f"**PHOTOS: {r['photos']}**", "",
            f"- stop reason: {r['stop_reason']}" + (f"; left unresolved: {r['left']}" if r.get("left") else ""),
            f"- slides built: {r['slides_built']} of {r['slides_requested']} requested",
            f"- tool calls: {r['tool_calls']} ({r['tool_failures']} failed); model turns: {r['model_calls']}; seconds: {r['seconds']}",
            f"- render-and-fix: {r['render_rounds']} slide views; slides viewed at least once: {r['slides_viewed']} of {r['slides_built']}; never viewed: {r['never_viewed'] or 'none'}",
            f"- cost: ${r['total_usd']:.4f} of the ${r['budget_usd']:.2f} budget" + ("; **ABORTED by the budget guard**" if r.get("aborted") else ""),
            f"- provider prompt cache: {r['cache_hit_rate']:.1f}% of input tokens read from cache",
            f"- failed or cut-off model calls: {r['failed_calls']} costing ${r['failed_cost']:.4f}",
            f"- hedging: {r['hedging']}", f"- layout mix: {r['layout_mix']}", f"- review list: {r['review_items']} items in review_before_use.md", "", r["usage"]]
    return NL.join(rows)


def run_agent(topic: str, audience: str, slides: int, run_dir: Path, *, budget: float, yes: bool, brand: str = "default_v1", tier_overrides: str | None = None,
              transport=None, search=None, fetcher=None, summarizer=None, builder=None, renderer=None, inspector=None, echo=print) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    brand_dir = CONFIG.brands_dir / brand
    roles = CONFIG.build_roles()
    model, haiku = roles["writer"].model, roles["extractor"].model
    ledger = Ledger(run_dir / "usage.jsonl", Prices.load(), budget, echo=echo)
    from deckgen.research.weigh import TierTable

    table = TierTable(CONFIG.root / "config" / "tier_overrides" / f"{tier_overrides}.json") if tier_overrides else TierTable()
    tools = AgentTools(run_dir, brand_dir, ledger, haiku_model=haiku, max_slides=slides - 1, tier_table=table, search=search, fetcher=fetcher,
                       summarizer=summarizer, builder=builder, renderer=renderer, inspector=inspector)
    loop = AgentLoop(tools, ledger, model=model, system_text=system_prompt(brand_dir), tool_defs=tool_definitions(), max_calls=CONFIG.agent_max_tool_calls,
                     max_seconds=CONFIG.agent_max_seconds, transport=transport or anthropic_transport, approval=_approval_hook(tools, yes, run_dir, echo))
    task = (f"Topic: {topic}{NL}Audience: {audience}{NL}Slides: at most {slides} in total, including the title slide, the closing slide and the sources slide.{NL}"
            + ("Approval: the outline is auto-approved; write outline.md, then carry on with the full spec." if yes else "Approval: write outline.md, then stop and wait for the user's approval.")
            + NL + "Work from the skill file instructions. Begin with research.")
    aborted = ""
    try:
        stats = loop.run(task)
    except BudgetAbort as e:
        aborted = str(e)
        stats = {"stop_reason": f"budget guard: {aborted}", "tool_calls": loop.tool_calls, "model_calls": loop.model_calls, "seconds": 0}
    except Exception as e:  # noqa: BLE001 - a crash must still leave the usage summary and the best deck
        stats = {"stop_reason": f"error: {type(e).__name__}: {str(e)[:200]}", "tool_calls": loop.tool_calls, "model_calls": loop.model_calls, "seconds": 0}
        ledger.aborted = ledger.aborted or stats["stop_reason"]
    left = _fallback(tools)
    if aborted:
        ledger.aborted = aborted
    ledger.write_summary(run_dir / "usage_summary.md")
    return _finish_report(topic, slides, run_dir, ledger, tools, stats, left, aborted, budget)


def _finish_report(topic, slides, run_dir: Path, ledger: Ledger, tools: AgentTools, stats: dict, left: str, aborted: str, budget: float) -> dict:
    from deckgen.pipeline import hedge_stats, layout_mix

    spec_path = run_dir / "deck_spec.json"
    spec = DeckSpec.model_validate_json(spec_path.read_text(encoding="utf-8")) if spec_path.exists() else None
    model_rows = [r for r in ledger.rows if r["role"] != "tool" and r["cache"] != "hit"]
    tin = sum(r["input_tokens"] for r in model_rows)
    cr = sum(r["cache_read_tokens"] for r in model_rows)
    cw = sum(r["cache_write_tokens"] for r in model_rows)
    bad = [r for r in model_rows if r["outcome"] != "ok"]
    notes = run_dir / "review_before_use.md"
    contact = bool(os.environ.get("DECKGEN_CONTACT"))
    n = len(spec.slides) if spec else 0
    r = {"run_id": run_dir.name, "topic": topic, "stop_reason": stats["stop_reason"], "left": left, "slides_built": n, "slides_requested": slides,
         "tool_calls": stats["tool_calls"], "tool_failures": sum(1 for x in ledger.rows if x["role"] == "tool" and x["outcome"] != "ok"), "model_calls": stats["model_calls"],
         "seconds": stats["seconds"], "render_rounds": sum(tools.rounds.values()), "slides_viewed": len(tools.viewed),
         "never_viewed": sorted(set(range(1, n + 1)) - tools.viewed), "total_usd": ledger.total, "budget_usd": budget, "aborted": aborted,
         "cache_hit_rate": 100 * cr / max(tin + cr + cw, 1), "failed_calls": len(bad), "failed_cost": sum(x["cost_usd"] for x in bad),
         "hedging": hedge_stats(spec) if spec else None, "layout_mix": layout_mix(spec) if spec else None,
         "review_items": sum(1 for line in notes.read_text(encoding="utf-8").splitlines() if line.strip().startswith(("-", "*"))) if notes.exists() else 0,
         "photos": "skipped: the agent loop has no photo tool in this version" + ("" if contact else "; DECKGEN_CONTACT is not set"),
         "usage": ledger.summary_md()}
    (run_dir / "run_report.md").write_text(report_md(r), encoding="utf-8")
    (run_dir / "agent_stats.json").write_text(json.dumps({k: v for k, v in r.items() if k != "usage"}, indent=1, default=str), encoding="utf-8")
    return r
