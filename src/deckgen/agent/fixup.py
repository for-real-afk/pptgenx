"""A bounded fix pass on a finished agent run: same loop, tools, ledger and phase budget, but no research and no first write. It starts in the look phase from
the previous run's notes, facts, outline and build script, with the instructions as the task, a small output limit per turn, and the previous deck as the
baseline (only slides whose text changed must be looked at again). Output goes to a NEW run directory; the source run is not touched."""
from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

from deckgen.agent.builder import fingerprint
from deckgen.agent.loop import AgentLoop, anthropic_transport
from deckgen.agent.phases import PhaseBudget
from deckgen.agent.run import _execute, system_prompt
from deckgen.agent.tools import AgentTools, tool_definitions
from deckgen.config import CONFIG
from deckgen.ledger import Ledger, Prices
from deckgen.qa.slide_text import extract
from deckgen.research.weigh import TierTable

NL = chr(10)
FIX_MAX_TOKENS = 8_000
CARRIED = ("build.js", "notes.md", "facts.md", "outline.md")
RESEARCH_TOOLS = {"search", "fetch_page", "summarize_sources"}


def source_table(src_run: Path, tiers: TierTable) -> str:
    """Every page the source run saved: path, URL, tier. Given to the agent so it never has to guess a URL or a tier."""
    index_file = src_run / "sources" / "index.json"
    index = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else {}
    def title(path: str) -> str:
        first = (src_run / path).read_text(encoding="utf-8").split(NL, 1)[0].lstrip("# ").strip()
        return first or "(untitled page)"

    return NL.join(f"- {path}: {title(path)} | {url} | tier {tiers.tier(url)}" for url, path in index.items())


def slide_texts(deck: Path, numbers: list[int]) -> str:
    return NL.join(f"slide {s.number} [{s.layout}] {s.title} | " + " | ".join(s.texts) + f" | notes: {s.notes}" for s in extract(deck) if s.number in numbers)


def run_fixup(src_run: Path, out_dir: Path, instructions: str, slides: list[int], *, budget: float, tier_overrides: str | None = None, brand: str = "default_v1",
              transport: Callable | None = None, inspector=None, renderer=None, echo: Callable[[str], None] = print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "work"
    work.mkdir(exist_ok=True)
    for name in CARRIED:
        if (src_run / "work" / name).exists():
            shutil.copy(src_run / "work" / name, work / name)
    if (src_run / "sources").exists():
        shutil.copytree(src_run / "sources", out_dir / "sources", dirs_exist_ok=True)
    baseline_deck = src_run / "deck.pptx"
    brand_dir = CONFIG.brands_dir / brand
    roles = CONFIG.build_roles()
    ledger = Ledger(out_dir / "usage.jsonl", Prices.load(), budget, echo=echo)
    phases = PhaseBudget(budget, 0.0, 0.0, CONFIG.agent_look_min_frac, CONFIG.agent_reserve_frac)
    phases.advance("look", "fix pass: starts in look-and-fix, research and first write are closed")
    table = TierTable(CONFIG.root / "config" / "tier_overrides" / f"{tier_overrides}.json") if tier_overrides else TierTable()
    tools = AgentTools(out_dir, brand_dir, ledger, phases, haiku_model=roles["extractor"].model, max_slides=99, max_pages=0, free_pages=0,
                       max_slide_rounds=CONFIG.agent_max_slide_rounds, tier_table=table, inspector=inspector, renderer=renderer)
    tools.builder.script = "build.js"
    tools.builder.baseline = fingerprint(baseline_deck)
    defs = [d for d in tool_definitions() if d["name"] not in RESEARCH_TOOLS]
    loop = AgentLoop(tools, ledger, phases, model=roles["writer"].model, system_text=system_prompt(brand_dir), tool_defs=defs, max_calls=CONFIG.agent_max_tool_calls,
                     max_seconds=CONFIG.agent_max_seconds, transport=transport or anthropic_transport, max_tokens=FIX_MAX_TOKENS)
    task = (f"FIX PASS on a finished deck. Research is closed and the deck is already written: work/build.js is the build script that produced the current deck "
            f"({len(tools.builder.baseline)} slides). Budget: ${budget:.2f} in total, so be economical: read only what you need, make the listed edits with edit_build_script "
            f"(not whole-file rewrites), run_build once, view only the slides you changed with render_slides, then write review.md and call finish.{NL}{NL}"
            f"INSTRUCTIONS:{NL}{instructions}{NL}{NL}CURRENT TEXT OF THE SLIDES TO CHANGE:{NL}{slide_texts(baseline_deck, slides)}{NL}{NL}"
            f"PAGES THE DECK WAS BUILT FROM (path: page title | url | tier); saved in sources/, read with read_source if you must check a claim:{NL}{source_table(src_run, table)}{NL}{NL}"
            f"Your earlier notes are in work/notes.md and work/facts.md. Slides you do not change are not re-checked: unchanged slides need no new look.")
    return _execute(loop, tools, ledger, phases, out_dir, f"fix pass on {src_run.name}", len(tools.builder.baseline), task)
