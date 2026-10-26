"""Facts sheet: built after research and before any slide is written. For every fact the findings state in conflicting ways (dates, numbers,
who did what, causes, sequence) it fixes ONE wording: the value of the best-weighted source, or "sources differ: X vs Y" when the weights tie.
Facts that rest on a single weak source are listed so the writer hedges them. The writer and the repair pass use only this wording, on every slide.
Grouping is judged by the checker model; choosing the value is deterministic (tier, independent origins, confidence), never the model's call."""
from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from deckgen.llm import LLM
from deckgen.research.weigh import weight
from deckgen.schemas.research import Finding

NL = chr(10)
MAX_GROUPS = 25
MAX_SINGLE = 40
SYSTEM = "You compare factual claims across sources. Output JSON only."


class Member(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    value: str = Field(description="what this finding says about the fact, in a few words (the date, number, name or sequence it states)")


class Group(BaseModel):
    model_config = ConfigDict(extra="ignore")
    topic: str = Field(description="the one fact being compared, e.g. 'when Audi withdrew from Group B'")
    members: list[Member]


class Groups(BaseModel):
    model_config = ConfigDict(extra="ignore")
    groups: list[Group] = Field(default_factory=list)


class Entry(BaseModel):
    topic: str
    status: str  # "chosen" | "differ"
    wording: str
    ids: list[str]


class Sheet(BaseModel):
    entries: list[Entry] = Field(default_factory=list)
    single_source: list[str] = Field(default_factory=list)  # "id (domain)": hedge these and name the source

    def text(self) -> str:
        if not self.entries and not self.single_source:
            return ""
        lines = ["FACTS SHEET: for these facts use EXACTLY this wording, on every slide that mentions them. Never state a second version."]
        for e in self.entries:
            tag = "SOURCES DIFFER" if e.status == "differ" else "USE"
            lines.append(f"- [{tag}] {e.topic}: {e.wording} (ids {', '.join(e.ids)})")
        if self.single_source:
            lines.append("- [HEDGE] these rest on one weak source: say 'reportedly' or 'according to <source>' when you use them: " + "; ".join(self.single_source))
        return NL.join(lines)


def domain(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")


def _norm(v: str) -> str:
    return " ".join(v.lower().split())


def decide(g: Group, by_id: dict[str, Finding]) -> Entry | None:
    """One value per fact. None when the members actually agree or too few are known findings."""
    ms = [m for m in g.members if m.id in by_id]
    if len(ms) < 2 or len({_norm(m.value) for m in ms}) < 2:
        return None
    ranked = sorted(ms, key=lambda m: weight(by_id[m.id]), reverse=True)
    best = ranked[0]
    rivals = [m for m in ranked[1:] if _norm(m.value) != _norm(best.value)]
    ids = [m.id for m in ranked]
    if all(weight(by_id[best.id]) > weight(by_id[m.id]) for m in rivals):
        return Entry(topic=g.topic, status="chosen", wording=f"{best.value} (best source: {domain(by_id[best.id].source_url)})", ids=ids)
    tied = [best] + [m for m in rivals if weight(by_id[m.id]) == weight(by_id[best.id])]
    seen: dict[str, str] = {}
    for m in tied:
        seen.setdefault(_norm(m.value), f"{m.value} ({domain(by_id[m.id].source_url)})")
    return Entry(topic=g.topic, status="differ", wording="sources differ: " + " vs ".join(seen.values()), ids=ids)


def build_sheet(llm: LLM, findings: list[Finding]) -> Sheet:
    usable = [f for f in findings if not f.excluded]
    by_id = {f.id: f for f in usable}
    listing = NL.join(f"{f.id} [t{f.tier} o{f.independent_origin_count}] {f.claim}" for f in usable)
    groups = llm.generate(
        stage="facts_sheet", system=SYSTEM, schema=Groups,
        prompt=f"FINDINGS:{NL}{listing}{NL}{NL}List every real-world fact that different findings state DIFFERENTLY: a different date, number, name, cause, sequence "
               f"or who-did-what (for example two power figures for one car, or two accounts of when someone withdrew). One group per fact: a topic and, for each "
               f"finding involved, the value it states. Skip facts on which all findings agree and skip findings about different things. At most {MAX_GROUPS} groups.")
    entries = [e for g in groups.groups[:MAX_GROUPS] if (e := decide(g, by_id))]
    single = [f"{f.id} ({domain(f.source_url)})" for f in usable if f.needs_review][:MAX_SINGLE]
    return Sheet(entries=entries, single_source=single)
