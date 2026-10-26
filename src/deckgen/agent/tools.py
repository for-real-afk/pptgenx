"""The agent's tool facade: one `call(name, args)` entry that gates tools by phase, logs every call to the ledger, and turns any failure into a message
the model can act on. The work is done by small collaborators: `Workspace` (files), `SourceReader` (research), `DeckBuilder` (build, look, finish)."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from deckgen.agent.builder import DeckBuilder, Inspector
from deckgen.agent.phases import PhaseBudget
from deckgen.agent.sources import SourceReader
from deckgen.agent.workspace import ToolError, Workspace
from deckgen.ledger import Ledger
from deckgen.research.weigh import TierTable

CLOSED_AFTER_RESEARCH = {"search", "fetch_page", "summarize_sources"}      # these spend money or pages; read_source only reads saved files
CLOSED_IN_WRAPUP = {"write_build_script", "run_build", "render_slides"}


class AgentTools:
    def __init__(self, run_dir: Path, brand_dir: Path, ledger: Ledger, phases: PhaseBudget, *, haiku_model: str, max_slides: int, max_pages: int, free_pages: int,
                 max_slide_rounds: int, tier_table: TierTable | None = None, search=None, summarizer: Callable[[str], str] | None = None,
                 inspector: Inspector | None = None, renderer: Callable[[Path, Path], None] | None = None,
                 page_loader: Callable[[str], dict] | None = None):
        self.ledger, self.phases = ledger, phases
        self.work = Workspace(run_dir / "work")
        self.reader = SourceReader(run_dir, self.work, ledger, haiku_model=haiku_model, max_pages=max_pages, free_pages=free_pages, tiers=tier_table,
                                   search=search, summarizer=summarizer, page_loader=page_loader)
        self.builder = DeckBuilder(run_dir, self.work, brand_dir, phases, max_slides=max_slides, max_slide_rounds=max_slide_rounds, inspector=inspector, renderer=renderer)
        self.lock = threading.Lock()
        self._handlers = {
            "search": self.reader.t_search, "fetch_page": self.reader.t_fetch_page, "read_source": self.reader.t_read_source,
            "summarize_sources": self.reader.t_summarize_sources, "write_file": self.t_write_file, "read_file": self.t_read_file,
            "write_build_script": self.builder.t_write_build_script, "run_build": self.builder.t_run_build, "render_slides": self.builder.t_render_slides,
            "slide_text": self.builder.t_slide_text, "finish": self.builder.t_finish,
        }

    @property
    def finished(self) -> bool:
        return self.builder.finished

    def start_wrapup(self) -> None:
        self.builder.wrapup = True

    # ---- dispatch ---------------------------------------------------------------------------------------------------------------------
    def _gate(self, name: str) -> None:
        if name in CLOSED_AFTER_RESEARCH and self.phases.phase != "research":
            raise ToolError("research is closed (its budget or phase is over). Work from notes.md, facts.md and the saved pages (read_source); put anything unsettled into review.md.")
        if name in CLOSED_IN_WRAPUP and self.phases.phase == "wrapup":
            raise ToolError("the budget for further build rounds is used up. Write review.md and call finish.")

    def call(self, name: str, args: dict):
        """(content, is_error). Content is text or a list of content blocks. Every call is logged to the ledger."""
        t0, err = time.time(), ""
        try:
            handler = self._handlers.get(name)
            if handler is None:
                raise ToolError(f"unknown tool '{name}'")
            self._gate(name)
            return handler(**args), False
        except ToolError as e:
            err = str(e)
        except TypeError as e:
            err = f"bad arguments for {name}: {e}"
        except Exception as e:  # noqa: BLE001 - any failure becomes a message the model can act on
            err = f"{name} failed: {type(e).__name__}: {str(e)[:300]}"
        finally:
            with self.lock:
                self.ledger.log_tool(name, time.time() - t0, not err, err)
        return err, True

    # ---- files ------------------------------------------------------------------------------------------------------------------------
    def t_write_file(self, path: str, content: str, append: bool = False) -> str:
        p = self.work.write(path, content, append)
        return f"{'appended to' if append else 'wrote'} work/{p.name}: {len(content)} chars"

    def t_read_file(self, path: str) -> str:
        return self.work.read(path)


def tool_definitions() -> list[dict]:
    def obj(**props):
        return {"type": "object", "properties": props, "required": [k for k, v in props.items() if not v.pop("optional", False)]}

    def txt():
        return {"type": "string"}

    return [
        {"name": "search", "description": "Web search (research phase). Returns titles, URLs, tiers, snippets. Junk domains are filtered.", "input_schema": obj(query=txt())},
        {"name": "fetch_page", "description": "Fetch a whole page, save it to sources/<n>.md; returns its path, length, tier and first 1,500 characters, plus links to higher-tier pages. "
         "The first pages are free; every later page needs gap set to text matching a 'GAP:' line you wrote in notes.md. Hard page cap.",
         "input_schema": obj(url=txt(), gap={"type": "string", "optional": True})},
        {"name": "read_source", "description": "Read a slice of a saved page: path from fetch_page, start offset in characters, length (default and max 12000).",
         "input_schema": obj(path=txt(), start={"type": "integer", "optional": True}, length={"type": "integer", "optional": True})},
        {"name": "summarize_sources", "description": "Cheap parallel reader (Haiku) over up to 6 saved pages: short quoted answers with file:line references. For breadth only.",
         "input_schema": obj(paths={"type": "array", "items": {"type": "string"}}, question=txt())},
        {"name": "write_file", "description": "Write a text file inside work/ (notes.md, facts.md, outline.md, review.md). append=true adds to the end.",
         "input_schema": obj(path=txt(), content=txt(), append={"type": "boolean", "optional": True})},
        {"name": "read_file", "description": "Read a file inside work/.", "input_schema": obj(path=txt())},
        {"name": "write_build_script", "description": "Write the pptxgenjs build script (a .js file inside work/). append=true writes a long script in parts.",
         "input_schema": obj(path=txt(), content=txt(), append={"type": "boolean", "optional": True})},
        {"name": "run_build", "description": "Run the build script in the restricted Node runner on the brand frame, apply the theme, then run file validation and the geometry checks. "
         "Returns errors with the script line, and per-slide issues.", "input_schema": obj(script_path={"type": "string", "optional": True})},
        {"name": "render_slides", "description": "Render a built deck through LibreOffice and return up to 4 slide images per call so you can look at them.",
         "input_schema": obj(deck_path=txt(), slides={"type": "array", "items": {"type": "integer"}})},
        {"name": "slide_text", "description": "The text extracted from a built deck, slide by slide (layout, title, texts, tables, charts, notes).", "input_schema": obj(deck_path=txt())},
        {"name": "finish", "description": "End the run with a built deck and your review notes file. Refused while the file is invalid, any slide overflows or leaves the slide, "
         "a slide was never looked at, or the notes are missing.", "input_schema": obj(deck_path=txt(), review_notes_path=txt())},
    ]
