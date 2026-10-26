"""Passage relevance filter: after the outline is approved and before the writer, a cheap model (Haiku) checks every collected passage against the
outline's questions (each slide's key message) and keeps only the passages that answer one of them. What was dropped is logged. Findings that
came from a dropped passage are dropped from the writer's pool too, so the writer never uses a fact from a dropped passage."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from deckgen.llm import LLM
from deckgen.research.passages import words
from deckgen.schemas.research import Finding, Findings, Outline

NL = chr(10)
CHUNK = 30
MATCH = 0.3  # share of a finding's words that must appear in a passage for that passage to be its source
SYSTEM = "You judge whether source passages answer a deck's questions. Output JSON only."


class Keep(BaseModel):
    model_config = ConfigDict(extra="ignore")
    i: int = Field(description="passage number")
    slides: list[int] = Field(default_factory=list, description="numbers of the outline questions this passage answers")


class KeepList(BaseModel):
    model_config = ConfigDict(extra="ignore")
    keep: list[Keep] = Field(default_factory=list)


class PassageFilter(BaseModel):
    kept: dict[str, list[str]] = Field(default_factory=dict)
    dropped: list[dict] = Field(default_factory=list)
    dropped_finding_ids: list[str] = Field(default_factory=list)


def source_passage(f: Finding, passages: list[str]) -> int | None:
    """Index of the passage a finding most likely came from (word overlap with its claim and evidence), or None."""
    fw = set(words(f.claim + " " + f.evidence_snippet))
    if not fw or not passages:
        return None
    scored = [(len(fw & set(words(p))) / len(fw), i) for i, p in enumerate(passages)]
    best, idx = max(scored)
    return idx if best >= MATCH else None


def filter_passages(llm: LLM, outline: Outline, findings: Findings) -> PassageFilter:
    questions = NL.join(f"{o.n}. {o.key_message}" for o in outline.slides if o.layout_hint not in ("TITLE", "SOURCES"))
    items = [(u, i, p) for u, ps in findings.passages.items() for i, p in enumerate(ps)]
    keep: set[tuple[str, int]] = set()
    for start in range(0, len(items), CHUNK):
        part = items[start:start + CHUNK]
        listing = NL.join(f"[{k}] {p[:700]}" for k, (_, _, p) in enumerate(part))
        res = llm.generate(stage="passage_filter", system=SYSTEM, schema=KeepList,
                           prompt=f"QUESTIONS (one per slide):{NL}{questions}{NL}{NL}PASSAGES:{NL}{listing}{NL}{NL}Return `keep`: only the passages that contain "
                                  f"facts answering at least one question, each with the numbers of the questions it answers. Omit passages that are off-topic, "
                                  f"generic, navigation text or about something else.")
        for k in res.keep:
            if 0 <= k.i < len(part) and k.slides:
                keep.add((part[k.i][0], part[k.i][1]))
    kept: dict[str, list[str]] = {}
    dropped: list[dict] = []
    for u, i, p in items:
        if (u, i) in keep:
            kept.setdefault(u, []).append(p)
        else:
            dropped.append({"url": u, "text": p[:160]})
    gone: list[str] = []
    for f in findings.findings:
        ps = findings.passages.get(f.source_url, [])
        idx = source_passage(f, ps)
        if idx is not None and (f.source_url, idx) not in keep:
            gone.append(f.id)
    return PassageFilter(kept=kept, dropped=dropped, dropped_finding_ids=gone)
