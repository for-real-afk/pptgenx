"""The agent's working folder (runs/<id>/work/): the only place it can write. Also reads the open research gaps the agent names in notes.md."""
from __future__ import annotations

import re
from pathlib import Path

WORDS = re.compile(r"[a-z0-9]+")
GAP_LINE = re.compile(r"^\s*(?:[-*]\s*)?GAP\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
GAP_MATCH = 0.6        # share of a fetch's stated gap words that must appear in a GAP line of notes.md
FETCHES_PER_GAP = 2


class ToolError(Exception):
    """A failure with a message the model can act on."""


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, rel: str) -> Path:
        """Resolve `rel` inside the folder. A leading 'work/' is accepted; anything that escapes the folder is refused."""
        text = rel.replace("\\", "/")
        if not Path(text).is_absolute():
            text = text.removeprefix(f"{self.root.name}/").lstrip("/")
        p = Path(text).resolve() if Path(text).is_absolute() else (self.root / text).resolve()
        if not p.is_relative_to(self.root):
            raise ToolError(f"path '{rel}' is outside {self.root.name}/; use a file name inside it")
        return p

    def write(self, rel: str, content: str, append: bool = False) -> Path:
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a" if append else "w", encoding="utf-8") as f:
            f.write(content)
        return p

    def read(self, rel: str) -> str:
        p = self.path(rel)
        if not p.exists():
            raise ToolError(f"{self.root.name}/{rel} does not exist")
        return p.read_text(encoding="utf-8", errors="replace")

    def exists(self, rel: str) -> bool:
        try:
            return self.path(rel).exists()
        except ToolError:
            return False

    def gaps(self) -> list[str]:
        """The gaps named in notes.md, one per `GAP:` line (what is missing or in doubt, and why it matters to a slide)."""
        return [m.group(1).strip() for m in GAP_LINE.finditer(self.read("notes.md"))] if self.exists("notes.md") else []


def words(text: str) -> set[str]:
    return set(WORDS.findall(text.lower()))


def matching_gap(stated: str, gaps: list[str]) -> int | None:
    """Index of the open gap that the stated gap refers to (most of the stated words appear in the GAP line), or None."""
    want = words(stated)
    if len(want) < 2:
        return None
    score, index = max(((len(want & words(g)) / len(want), i) for i, g in enumerate(gaps)), default=(0.0, None))
    return index if score >= GAP_MATCH else None
