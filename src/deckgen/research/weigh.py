"""Weighing evidence. Tiers (config-driven), independent origins, corroboration, and conflict resolution BY WEIGHT, not by count.

weight = (source tier, independent origins, confidence). Two low-tier pages that copy each other are ONE origin and cannot
outvote a single authoritative source. Conflicts that cannot be settled by weight are recorded with every value and source,
the findings are excluded from slides, and both appear in review_before_use.md."""
from __future__ import annotations

import json
import os
import re
from itertools import combinations
from pathlib import Path
from urllib.parse import urlparse

from deckgen.config import CONFIG
from deckgen.llm import LLM
from deckgen.research.passages import similarity, words
from deckgen.schemas.research import ConflictRecord, ConflictReport, Finding, PairVerdicts

NUM = re.compile(r"\d[\d,]*\.?\d*")
CONF_RANK = {"low": 0, "medium": 1, "high": 2}
STRONG = re.compile(r"\b(first|only|all|every|always|never|none|exponential\w*|unprecedented|unmatched|greatest|best|entire|"
                    r"concluded|determined|announced|investigation|found that|banned|prohibited)\b", re.I)


def host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def registrable(url: str) -> str:
    return ".".join(host(url).split(".")[-2:])


class TierTable:
    """Default tiers from config/source_tiers.json plus an optional per-topic override file (config, never code)."""

    def __init__(self, override: Path | None = None):
        base = json.loads((CONFIG.root / "config" / "source_tiers.json").read_text(encoding="utf-8"))
        self.default = base.get("default_tier", 1)
        self.rules = list(base["rules"])
        path = override or (Path(os.environ["DECKGEN_TIER_OVERRIDES"]) if os.environ.get("DECKGEN_TIER_OVERRIDES") else None)
        if path and Path(path).exists():
            self.rules = json.loads(Path(path).read_text(encoding="utf-8")).get("rules", []) + self.rules  # overrides win

    def tier(self, url: str) -> int:
        h = host(url)
        for r in self.rules:
            if any(h == d or h.endswith("." + d) for d in r.get("domains", [])) or any(h.endswith(s) for s in r.get("suffixes", [])) \
                    or any(c in url.lower() for c in r.get("contains", [])):
                return r["tier"]
        return self.default


def assign_tiers_origins(findings: list[Finding], table: TierTable) -> list[Finding]:
    """Origin = publisher (registrable domain) merged with any page that is a near-copy of another."""
    parent: dict[str, str] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pages: dict[str, list[int]] = {}
    for f in findings:
        pages.setdefault(f.source_url, f.page_sig)
        parent.setdefault(registrable(f.source_url), registrable(f.source_url))
    urls = list(pages)
    for a, b in combinations(urls, 2):
        if registrable(a) != registrable(b) and similarity(pages[a], pages[b]) >= 0.5:  # mirror / republished text
            parent[find(registrable(a))] = find(registrable(b))
    ids: dict[str, str] = {}
    out = []
    for f in findings:
        root = find(registrable(f.source_url))
        oid = ids.setdefault(root, f"o{len(ids) + 1:02d}")
        brand = registrable(f.source_url).split(".")[0]
        t = table.tier(f.source_url)
        self_src = t <= 1 and len(brand) > 3 and brand in f.claim.lower()
        out.append(f.model_copy(update={"tier": t, "origin_id": oid, "self_sourced": self_src}))
    return out


def _tokset(t: str) -> set[str]:
    return {w for w in words(t) if len(w) > 3 and not w.isdigit()}


def corroborate(findings: list[Finding]) -> list[Finding]:
    """Independent origins that state the same thing. Self-sourced vendor pages do not count towards their own claims."""
    toks = [_tokset(f.claim) for f in findings]
    out = []
    for i, f in enumerate(findings):
        origins = set() if f.self_sourced else {f.origin_id}
        for j, g in enumerate(findings):
            if i != j and g.origin_id != f.origin_id and not g.self_sourced and toks[i] and toks[j] and \
                    len(toks[i] & toks[j]) / len(toks[i] | toks[j]) >= 0.4:
                origins.add(g.origin_id)
        out.append(f.model_copy(update={"independent_origin_count": len(origins), "corroborated_by": max(len(origins), 1)}))
    return out


def mark_review(findings: list[Finding]) -> list[Finding]:
    """A strong or numeric claim resting on one low-tier origin must be attributed and checked by a human."""
    out = []
    for f in findings:
        weak = f.tier < 3 and f.independent_origin_count < 2 and bool(STRONG.search(f.claim) or NUM.search(f.claim))
        out.append(f.model_copy(update={"needs_review": weak, "review_reason": "strong or numeric claim from one low-tier origin" if weak else ""}))
    return out


def weight(f: Finding) -> tuple:
    return (f.tier, f.independent_origin_count, CONF_RANK[f.confidence])


def _nums(t: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM.findall(t)}


def candidate_pairs(findings: list[Finding], limit: int = 60) -> list[tuple[int, int]]:
    """Deterministic candidates: nearly the same words but different numbers (a contradiction OR two distinct facts: the checker decides)."""
    cands = []
    toks = [_tokset(f.claim) for f in findings]
    for i, j in combinations(range(len(findings)), 2):
        if not toks[i] or not toks[j]:
            continue
        ni, nj = _nums(findings[i].claim), _nums(findings[j].claim)
        if not ni or not nj or ni == nj or ni <= nj or nj <= ni:
            continue
        jac = len(toks[i] & toks[j]) / len(toks[i] | toks[j])
        if jac >= 0.6:
            cands.append((jac, i, j))
    return [(i, j) for _, i, j in sorted(cands, reverse=True)[:limit]]


def judge_pairs(llm: LLM, findings: list[Finding], pairs: list[tuple[int, int]]) -> set[frozenset]:
    out: set[frozenset] = set()
    known = {f.id for f in findings}
    if pairs:
        listing = "\n".join(f"P{n}: [{findings[i].id}] {findings[i].claim}  VS  [{findings[j].id}] {findings[j].claim}" for n, (i, j) in enumerate(pairs, 1))
        v = llm.generate(stage="research_conflict_pairs", system="You judge whether two claims contradict. Output JSON only.", schema=PairVerdicts,
                         prompt="For each pair, decide whether the two claims state the SAME fact with INCOMPATIBLE values (for example two different "
                                "years for one event). Claims about different events, people or things are NOT contradictions even if worded alike. "
                                f"Return only the contradicting pairs as ids a and b.\n\n{listing}")
        out |= {frozenset((p.a, p.b)) for p in v.contradictions if p.a in known and p.b in known and p.a != p.b}
    if len(findings) >= 2:  # the general pass catches contradictions without numbers
        listing = "\n".join(f"{f.id}: {f.claim}" for f in findings[:120])
        rep = llm.generate(stage="research_crosscheck", system="You compare factual claims. Output JSON only.", schema=ConflictReport,
                           prompt="List claims that CONTRADICT each other on the same fact. Ignore claims that merely differ in topic. For each finding "
                                  f"with contradictions give its id and the ids it conflicts with.\n\n{listing}")
        for link in rep.links:
            for o in link.conflicts_with:
                if link.id in known and o in known and o != link.id:
                    out.add(frozenset((link.id, o)))
    return out


def resolve(findings: list[Finding], confirmed: set[frozenset]) -> tuple[list[Finding], list[ConflictRecord]]:
    by_id = {f.id: f for f in findings}
    excluded: dict[str, str] = {}
    records: list[ConflictRecord] = []
    links: dict[str, set[str]] = {}
    for pair in sorted(confirmed, key=lambda p: sorted(p)):
        a, b = (by_id[x] for x in sorted(pair))
        links.setdefault(a.id, set()).add(b.id)
        links.setdefault(b.id, set()).add(a.id)
        wa, wb = weight(a), weight(b)
        rec = dict(ids=[a.id, b.id], claims=[a.claim, b.claim], sources=[a.source_url, b.source_url])
        if wa != wb:
            win, lose = (a, b) if wa > wb else (b, a)
            excluded.setdefault(lose.id, f"outweighed by {win.id}: tier {win.tier} vs {lose.tier}, origins {win.independent_origin_count} vs {lose.independent_origin_count}")
            records.append(ConflictRecord(**rec, status="resolved", winner=win.id, reason=excluded[lose.id]))
        else:
            for f, other in ((a, b), (b, a)):
                excluded.setdefault(f.id, f"contested with {other.id}: equal weight {wa}")
            records.append(ConflictRecord(**rec, status="unresolved", reason=f"equal weight {wa}: sources differ, attribute or omit"))
    out = [f.model_copy(update={"conflicts_with": sorted(links.get(f.id, [])), "excluded": f.id in excluded,
                                "excluded_reason": excluded.get(f.id, "")}) for f in findings]
    return out, records


def weigh(llm: LLM, findings: list[Finding], table: TierTable | None = None) -> tuple[list[Finding], list[ConflictRecord]]:
    f = assign_tiers_origins(findings, table or TierTable())
    f = mark_review(corroborate(f))
    f, records = resolve(f, judge_pairs(llm, f, candidate_pairs(f)))
    return mark_review(corroborate(f)), records
