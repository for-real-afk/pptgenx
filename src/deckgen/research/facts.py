"""Facts sheet: built after research and before any slide is written. One structured record per (subject, attribute) that the findings state in
conflicting ways (dates, numbers, who did what, causes, sequence): the chosen value (the best-weighted source) and status resolved, or
"sources differ: X vs Y" when the weights tie. Records about the same subject and attribute are merged. Facts that rest on a single weak source
are listed so the writer hedges them. The writer and the repair pass use only the chosen wording, on every slide, and `facts_problems` is a
deterministic check that two slides never state different values for the same attribute.
Grouping is judged by the checker model; choosing the value is deterministic (tier, independent origins, confidence), never the model's call."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from deckgen.llm import LLM
from deckgen.research.weigh import weight
from deckgen.schemas import DeckSpec
from deckgen.schemas.research import Finding
from deckgen.spec.claims import NUM_RE, name_core, visible_fields
from deckgen.spec.deck_checks import Problem

NL = chr(10)
MAX_GROUPS = 30
MAX_SINGLE = 40
MERGE_JACCARD = 0.6
SYSTEM = "You compare factual claims across sources. Output JSON only."
HEDGE_WORDS = re.compile("(differ|reportedly|according to|approximately|roughly|about|around|estimated)", re.I)
STOP = {"the", "and", "for", "with", "from", "that", "this", "was", "were", "has", "had", "their", "when", "after", "before", "during"}


class Member(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    value: str = Field(description="what this finding says about the attribute, in a few words (the date, number, name or sequence it states)")


class Group(BaseModel):
    model_config = ConfigDict(extra="ignore")
    subject: str = Field(description="the thing the fact is about, short and canonical, e.g. 'Audi' or 'Lancia Delta S4'")
    attribute: str = Field(description="the property compared, short and canonical, e.g. 'withdrawal timing' or 'competition power'")
    members: list[Member]


class Groups(BaseModel):
    model_config = ConfigDict(extra="ignore")
    groups: list[Group] = Field(default_factory=list)


class Entry(BaseModel):
    subject: str
    attribute: str
    status: str            # "resolved" | "differ"
    value: str             # resolved: the chosen value. differ: "sources differ: X (domain) vs Y (domain)"
    tier: int = 0          # tier of the best source
    source: str = ""       # domain of the best source
    values: list[str] = Field(default_factory=list)   # every candidate value the findings stated
    ids: list[str] = Field(default_factory=list)


class Sheet(BaseModel):
    entries: list[Entry] = Field(default_factory=list)
    single_source: list[str] = Field(default_factory=list)  # "id (domain)": hedge these and name the source

    def text(self) -> str:
        if not self.entries and not self.single_source:
            return ""
        lines = ["FACTS SHEET: for these facts use EXACTLY the chosen wording, on every slide that mentions them. Never state a second version."]
        for e in self.entries:
            if e.status == "differ":
                lines.append(f"- [SOURCES DIFFER] {e.subject} / {e.attribute}: {e.value}. Say that the sources differ; do not pick one. (ids {', '.join(e.ids)})")
            else:
                lines.append(f"- [USE] {e.subject} / {e.attribute}: {e.value} (tier {e.tier}, {e.source}; ids {', '.join(e.ids)})")
        if self.single_source:
            lines.append("- [HEDGE] these rest on one weak source: say 'reportedly' or 'according to <source>' when you use them: " + "; ".join(self.single_source))
        return NL.join(lines)


def domain(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")


def _norm(v: str) -> str:
    return " ".join(v.lower().split())


def _toks(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in STOP and len(w) > 2}


def merge_groups(groups: list[Group]) -> list[Group]:
    """Groups about the same subject and attribute become one (exact key, or token overlap of subject+attribute >= MERGE_JACCARD)."""
    out: list[tuple[set[str], Group]] = []
    for g in groups:
        key = _toks(g.subject + " " + g.attribute)
        for k, tgt in out:
            if k and key and len(k & key) / len(k | key) >= MERGE_JACCARD:
                seen = {m.id for m in tgt.members}
                tgt.members.extend(m for m in g.members if m.id not in seen)
                break
        else:
            out.append((key, g.model_copy(update={"members": list(g.members)})))
    return [g for _, g in out]


def decide(g: Group, by_id: dict[str, Finding]) -> Entry | None:
    """One value per fact. None when the members actually agree or too few are known findings."""
    ms = [m for m in g.members if m.id in by_id]
    if len(ms) < 2 or len({_norm(m.value) for m in ms}) < 2:
        return None
    ranked = sorted(ms, key=lambda m: weight(by_id[m.id]), reverse=True)
    best = ranked[0]
    rivals = [m for m in ranked[1:] if _norm(m.value) != _norm(best.value)]
    base = dict(subject=g.subject, attribute=g.attribute, values=list(dict.fromkeys(m.value for m in ranked)), ids=[m.id for m in ranked])
    if all(weight(by_id[best.id]) > weight(by_id[m.id]) for m in rivals):
        return Entry(**base, status="resolved", value=best.value, tier=by_id[best.id].tier, source=domain(by_id[best.id].source_url))
    tied = [best] + [m for m in rivals if weight(by_id[m.id]) == weight(by_id[best.id])]
    seen: dict[str, str] = {}
    for m in tied:
        seen.setdefault(_norm(m.value), f"{m.value} ({domain(by_id[m.id].source_url)})")
    return Entry(**base, status="differ", value="sources differ: " + " vs ".join(seen.values()), tier=by_id[best.id].tier, source=domain(by_id[best.id].source_url))


def build_sheet(llm: LLM, findings: list[Finding]) -> Sheet:
    usable = [f for f in findings if not f.excluded]
    by_id = {f.id: f for f in usable}
    listing = NL.join(f"{f.id} [t{f.tier} o{f.independent_origin_count}] {f.claim}" for f in usable)
    groups = llm.generate(
        stage="facts_sheet", system=SYSTEM, schema=Groups,
        prompt=f"FINDINGS:{NL}{listing}{NL}{NL}List every real-world fact that different findings state DIFFERENTLY: a different date, number, name, cause, sequence "
               f"or who-did-what (for example two power figures for one car, or two accounts of when someone withdrew). One group per (subject, attribute): give a short "
               f"canonical subject and attribute (the same words every time the same fact comes up) and, for each finding involved, the value it states. Skip facts on "
               f"which all findings agree and skip findings about different things. At most {MAX_GROUPS} groups.")
    entries = [e for g in merge_groups(groups.groups[:MAX_GROUPS]) if (e := decide(g, by_id))]
    single = [f"{f.id} ({domain(f.source_url)})" for f in usable if f.needs_review][:MAX_SINGLE]
    return Sheet(entries=entries, single_source=single)


# ---- deterministic check: two slides never state different values for the same facts-sheet attribute -------------------------
def key_tokens(value: str) -> list[str]:
    nums = [n.replace(",", "").rstrip(".") for n in NUM_RE.findall(value)]
    names = [name_core(m) for m in re.findall(r"\b[A-Z][\w'-]{2,}(?:\s+[A-Z][\w'-]{2,})*", value)]
    return [t.lower() for t in nums + names if t]


def _states(text: str, entry: Entry, value: str) -> bool:
    """The slide is about the entry's subject and states this value (all its numbers/names appear)."""
    subj = _toks(entry.subject)
    if subj and len(subj & _toks(text)) / len(subj) < 0.5:
        return False
    keys = key_tokens(value)
    t = text.lower().replace(",", "")
    return bool(keys) and all(k in t for k in keys)


def facts_problems(spec: DeckSpec, sheet: Sheet) -> list[Problem]:
    out: list[Problem] = []
    texts = {i: " ".join(t for _, t in visible_fields(s)) for i, s in enumerate(spec.slides, 1) if s.layout not in ("SOURCES", "TITLE")}
    for e in sheet.entries:
        stated: dict[int, set[str]] = {}
        for i, t in texts.items():
            vs = {_norm(v) for v in e.values if _states(t, e, v)}
            if vs:
                stated[i] = vs
        for i, vs in stated.items():
            wrong = vs - ({_norm(e.value)} if e.status == "resolved" else set())
            if e.status == "resolved" and wrong and _norm(e.value) not in vs:
                out.append(Problem(kind="contradiction", slides=[i], description=f"slide {i} states a value for {e.subject} / {e.attribute} that differs from the facts sheet ({e.value})"))
        idx = sorted(stated)
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                if stated[idx[a]] != stated[idx[b]] and not (stated[idx[a]] <= stated[idx[b]] or stated[idx[b]] <= stated[idx[a]]):
                    out.append(Problem(kind="contradiction", slides=[idx[a], idx[b]],
                                       description=f"slides {idx[a]} and {idx[b]} state different values for {e.subject} / {e.attribute}: {sorted(stated[idx[a]])} vs {sorted(stated[idx[b]])}"))
    return out
