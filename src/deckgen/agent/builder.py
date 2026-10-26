"""Build, look and hand over: the agent writes a pptxgenjs build script, `run_build` runs it in the restricted Node runner on the brand frame, applies
the theme, then runs the existing file validation and geometry checks. `render_slides` returns slide images, `slide_text` the extracted text, and `finish`
ends the run only when the deck is valid, nothing overflows or leaves the slide, every slide was looked at and review notes exist."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from deckgen.agent.phases import PhaseBudget
from deckgen.agent.workspace import ToolError, Workspace
from deckgen.config import CONFIG
from deckgen.schemas.qa import Issue
from deckgen.spec.validate_spec import load_manifest

NL = chr(10)
MAX_IMAGES = 4
IMAGE_WIDTH = 1000
BUILD_TIMEOUT_S = 120
HARD_TYPES = {"overflow", "off_slide"}
KEEP_ENV = ("PATH", "SystemRoot", "TEMP", "TMP", "HOME", "USERPROFILE")
Inspector = Callable[[Path], list[Issue]]


@dataclass
class BuildRecord:
    deck: Path
    slides: int
    issues: list[Issue] = field(default_factory=list)

    @property
    def file_errors(self) -> list[Issue]:
        return [i for i in self.issues if i.source == "file"]

    @property
    def hard(self) -> list[Issue]:
        return [i for i in self.issues if i.type in HARD_TYPES or i.source == "file"]

    @property
    def blocking(self) -> list[Issue]:
        from deckgen.qa import policy

        return policy.blocking(self.issues)

    @property
    def rank(self) -> tuple[int, int]:
        """Lower is better: hard problems first, then all blocking ones."""
        return len(self.hard), len(self.blocking)


def default_inspector(brand_dir: Path) -> Inspector:
    from deckgen.qa import checks, validate_pptx
    from deckgen.qa.slide_text import content_issues, extract

    names = {lay["name"] for lay in load_manifest(brand_dir)["layouts"]}

    def inspect(deck: Path) -> list[Issue]:
        file_issues = [Issue(slide=1, type="other", severity="high", description=e, suggested_fix="fix the build script", source="file") for e in validate_pptx.validate(deck, names)]
        return file_issues + content_issues(extract(deck)) + checks.check(deck)

    return inspect


def default_renderer(deck: Path, out: Path) -> None:
    from deckgen.qa import soffice

    soffice.render_deck(deck, out)


class DeckBuilder:
    def __init__(self, run_dir: Path, work: Workspace, brand_dir: Path, phases: PhaseBudget, *, max_slides: int, max_slide_rounds: int,
                 inspector: Inspector | None = None, renderer: Callable[[Path, Path], None] | None = None):
        self.run_dir, self.work, self.brand_dir, self.phases = run_dir, work, brand_dir, phases
        self.max_slides, self.max_views = max_slides, 1 + max_slide_rounds
        self._inspect = inspector or default_inspector(brand_dir)
        self._render = renderer or default_renderer
        self.builds: dict[str, BuildRecord] = {}
        self.build_count = 0
        self.views: dict[int, int] = {}
        self.script: str | None = None
        self.finished = False
        self.wrapup = False

    # ---- script and build -------------------------------------------------------------------------------------------------------------
    def t_write_build_script(self, path: str, content: str, append: bool = False) -> str:
        if not path.endswith(".js"):
            raise ToolError("a build script must be a .js file inside work/")
        p = self.work.write(path, content, append)
        self.script = p.name
        self.phases.advance("write", "build script started")
        return f"{'appended to' if append else 'wrote'} work/{p.name}: {p.stat().st_size} bytes. Run it with run_build."

    def _node(self, script: Path, out: Path) -> dict:
        env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}      # no API keys or other secrets reach the script's process
        cmd = ["node", str(CONFIG.renderer_dir / "run_script.js"), str(script), str(self.brand_dir), str(out), str(self.work.root)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=BUILD_TIMEOUT_S, env=env, cwd=str(self.work.root))
        except subprocess.TimeoutExpired as e:
            raise ToolError(f"the build took longer than {BUILD_TIMEOUT_S}s and was stopped") from e
        lines = [x for x in r.stdout.splitlines() if x.strip()]
        try:
            return json.loads(lines[-1])
        except (IndexError, json.JSONDecodeError) as e:
            raise ToolError(f"the build runner failed: {(r.stderr or r.stdout)[-400:]}") from e

    def t_run_build(self, script_path: str = "") -> str:
        if self.wrapup:
            raise ToolError("the budget for further build rounds is used up; write review.md and call finish")
        script = self.work.path(script_path or self.script or "build.js")
        if not script.exists():
            raise ToolError(f"work/{script.name} does not exist; write it with write_build_script")
        self.build_count += 1
        deck = self.work.root / f"deck_{self.build_count}.pptx"
        result = self._node(script, deck)
        if not result.get("ok"):
            where = f" at line {result['line']}" if result.get("line") else ""
            raise ToolError(f"build script failed{where}: {result.get('error', 'unknown error')}")
        record = BuildRecord(deck=deck, slides=int(result["slides"]), issues=self._inspect(deck))
        self.builds[str(deck)] = record
        self.phases.advance("look", "first build")
        return self._report(record)

    @staticmethod
    def _report(r: BuildRecord) -> str:
        hard = sum(i.type in HARD_TYPES for i in r.issues)
        head = (f"built work/{r.deck.name}: {r.slides} slides. File validation: {'OK' if not r.file_errors else str(len(r.file_errors)) + ' errors'}. "
                f"Overflow or off-slide issues: {hard}. Other blocking issues: {len(r.blocking) - sum(i.type in HARD_TYPES for i in r.blocking)}.")
        lines = [f"slide {i.slide} [{i.severity}] {i.type}: {i.description[:200]}" for i in sorted(r.issues, key=lambda x: x.slide)][:40]
        return head + NL + (NL.join(lines) or "no automated issues")

    def best(self) -> BuildRecord | None:
        """The build with the fewest hard, then blocking, issues; the latest wins ties."""
        return min(reversed(list(self.builds.values())), key=lambda r: r.rank, default=None)

    # ---- look -------------------------------------------------------------------------------------------------------------------------
    def _deck(self, deck_path: str) -> Path:
        deck = self.work.path(deck_path)
        if not deck.exists():
            raise ToolError(f"work/{deck_path} does not exist; run_build returns the file name")
        return deck

    def t_render_slides(self, deck_path: str, slides: list[int]):
        if self.wrapup:
            raise ToolError("the budget for further render rounds is used up; write review.md and call finish")
        deck = self._deck(deck_path)
        out = self.work.root / "render" / hashlib.sha256(deck.read_bytes()).hexdigest()[:12]
        if not out.exists():
            out.mkdir(parents=True)
            self._render(deck, out)
        images = sorted((out / "images").glob("slide-*.jpg"), key=lambda p: int(p.stem.split("-")[1]))
        blocks: list[dict] = []
        for n in list(dict.fromkeys(slides))[:MAX_IMAGES]:
            if not 1 <= n <= len(images):
                raise ToolError(f"slide {n} does not exist (the deck has {len(images)} slides)")
            if self.views.get(n, 0) >= self.max_views:
                blocks.append({"type": "text", "text": f"slide {n}: already viewed {self.max_views} times (the first look plus {self.max_views - 1} fix rounds); stop changing it, the best build is kept"})
                continue
            self.views[n] = self.views.get(n, 0) + 1
            blocks += [{"type": "text", "text": f"slide {n} (view {self.views[n]} of {self.max_views})"}, self._image(images[n - 1], out / f"s{n}.jpg")]
        return blocks

    @staticmethod
    def _image(src: Path, dst: Path) -> dict:
        if not dst.exists():
            from PIL import Image

            im = Image.open(src).convert("RGB")
            im.resize((IMAGE_WIDTH, int(im.height * IMAGE_WIDTH / im.width))).save(dst, "JPEG", quality=80)
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(dst.read_bytes()).decode()}}

    def t_slide_text(self, deck_path: str) -> str:
        from deckgen.qa.slide_text import extract, to_markdown

        return to_markdown(extract(self._deck(deck_path)))

    # ---- end --------------------------------------------------------------------------------------------------------------------------
    def refusals(self, record: BuildRecord, notes: Path) -> list[str]:
        out = []
        if record.file_errors:
            out.append(f"file validation fails: {record.file_errors[0].description[:120]}")
        stuck = sorted({i.slide for i in record.issues if i.type in HARD_TYPES})
        if stuck:
            out.append(f"overflow or off-slide issues on slides {stuck}")
        unseen = sorted(set(range(1, record.slides + 1)) - set(self.views))
        if unseen:
            out.append(f"you have not looked at slides {unseen}: call render_slides on them and fix what you see first")
        if record.slides > self.max_slides:
            out.append(f"{record.slides} slides; the maximum is {self.max_slides}")
        if not notes.exists():
            out.append(f"review notes file work/{notes.name} does not exist")
        return out

    def t_finish(self, deck_path: str, review_notes_path: str) -> str:
        deck = self._deck(deck_path)
        record = self.builds.get(str(deck))
        if record is None:
            raise ToolError("that deck was not built by run_build")
        notes = self.work.path(review_notes_path)
        problems = self.refusals(record, notes)
        if problems:
            raise ToolError("cannot finish: " + "; ".join(problems))
        self.finalize(record, notes)
        return "finished: deck.pptx and review_before_use.md written"

    def finalize(self, record: BuildRecord, notes: Path | None) -> None:
        shutil.copy(record.deck, self.run_dir / "deck.pptx")
        if self.script and (self.work.root / self.script).exists():
            shutil.copy(self.work.root / self.script, self.run_dir / "build_script.js")
        body = notes.read_text(encoding="utf-8") if notes and notes.exists() else f"# Review before use{NL}{NL}- The agent did not write review notes: nothing was verified for you."
        (self.run_dir / "review_before_use.md").write_text(body, encoding="utf-8")
        self.finished = True
