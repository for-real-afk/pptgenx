"""Fixer output: a small, flat diff against one slide. Word growth is forbidden and enforced in code."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from deckgen.schemas.deck import SlideSpec

_SKIP = {"id", "section", "layout", "icon", "kind", "type", "source_refs", "source_caption", "speaker_notes", "number_format"}


class Edit(BaseModel):
    model_config = ConfigDict(extra="ignore")  # model-facing: a stray key such as "reason" must not fail the whole response
    action: Literal["replace", "delete"]
    path: str  # JSON pointer into the slide, e.g. /placeholders/body/1 or /composition/items/2/body
    text: str = ""


class EditList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edits: list[Edit]


class EditRejected(ValueError):
    pass


def word_count(node: Any, key: str = "") -> int:
    if key in _SKIP:
        return 0
    if isinstance(node, str):
        return len(node.split())
    if isinstance(node, dict):
        return sum(word_count(v, k) for k, v in node.items())
    if isinstance(node, list):
        return sum(word_count(v, key) for v in node)
    return 0


def slide_words(slide: SlideSpec) -> int:
    return word_count(slide.model_dump(exclude_none=True))


def _walk(root: Any, parts: list[str]) -> tuple[Any, str]:
    cur = root
    for p in parts[:-1]:
        cur = cur[int(p)] if isinstance(cur, list) else cur[p]
    return cur, parts[-1]


def apply_edits(slide: SlideSpec, edits: list[Edit], allow_growth: int = 0) -> SlideSpec:
    """Apply edits to a copy. Raises EditRejected if an edit is invalid, lengthens text, or total words grow."""
    data = slide.model_dump(exclude_none=True)
    before = word_count(data)
    deletes: list[tuple[list, int]] = []
    for e in edits:
        parts = [p for p in e.path.split("/") if p]
        if not parts or parts[0] in _SKIP:
            raise EditRejected(f"path not editable: {e.path}")
        try:
            parent, last = _walk(data, parts)
            if e.action == "replace":
                old = parent[int(last)] if isinstance(parent, list) else parent[last]
                if not isinstance(old, str):
                    raise EditRejected(f"{e.path} is not text")
                if not allow_growth and len(e.text.split()) > len(old.split()):
                    raise EditRejected(f"edit lengthens {e.path}")
                if isinstance(parent, list):
                    parent[int(last)] = e.text
                else:
                    parent[last] = e.text
            elif isinstance(parent, list):
                deletes.append((parent, int(last)))
            else:
                raise EditRejected(f"can only delete list items: {e.path}")
        except EditRejected:
            raise
        except (KeyError, IndexError, ValueError, TypeError) as ex:
            raise EditRejected(f"bad path {e.path}: {ex}") from ex
    for parent, idx in sorted(deletes, key=lambda t: -t[1]):  # high indices first
        del parent[idx]
    if word_count(data) > before + allow_growth:
        raise EditRejected("net word growth")
    try:
        return SlideSpec.model_validate(data)
    except ValidationError as ex:
        raise EditRejected(f"edit breaks schema: {str(ex)[:200]}") from ex
