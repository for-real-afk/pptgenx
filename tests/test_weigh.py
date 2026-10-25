"""Evidence weighing: tiers, origin collapse, conflict resolution by weight (not count), review list entries."""
from pathlib import Path

from deckgen.llm import FakeLLM
from deckgen.research import passages
from deckgen.research.weigh import TierTable, candidate_pairs, weigh
from deckgen.schemas.research import Finding

PAGE = ("Group A replaced Group B for the 1987 season after the fatal accidents of 1986. " * 8)


def f(i, claim, url, typ="journalism", page=PAGE, conf="high"):
    return Finding(id=f"f{i:02d}", question="q", claim=claim, evidence_snippet="s", source_type=typ, source_url=url,
                   retrieved_at="2026-01-01T00:00:00+00:00", confidence=conf, page_sig=passages.signature(page))


def llm_for(*pairs):
    return FakeLLM({"research_conflict_pairs": {"contradictions": [{"a": a, "b": b} for a, b in pairs]}, "research_crosscheck": {"links": []}})


def test_two_weak_copies_do_not_outvote_one_official_source():
    findings = [
        f(1, "Group A replaced Group B in the 1986 season", "https://blog-one.example.com/post", "other"),
        f(2, "Group A replaced Group B in the 1986 season", "https://blog-two.example.net/copy", "other"),   # republished copy: same page text
        f(3, "Group A replaced Group B in the 1987 season", "https://history.agency.gov/rules", "official", page="Completely different text. " * 30),
    ]
    out, conflicts = weigh(llm_for(("f01", "f03"), ("f02", "f03")), findings, TierTable())
    by = {x.id: x for x in out}
    assert by["f01"].origin_id == by["f02"].origin_id != by["f03"].origin_id     # the copies collapse into ONE origin
    assert by["f03"].tier == 4 and not by["f03"].excluded                          # the official finding stands
    assert by["f01"].excluded and by["f02"].excluded and "outweighed by f03" in by["f01"].excluded_reason
    assert all(c.status == "resolved" and c.winner == "f03" for c in conflicts)


def test_equal_weight_conflict_is_recorded_with_both_values_and_both_excluded():
    findings = [f(1, "The ban took effect for the 1986 season", "https://a.example.org/x", "other", page="alpha text " * 40),
                f(2, "The ban took effect for the 1987 season", "https://b.example.net/y", "other", page="beta words " * 40)]
    out, conflicts = weigh(llm_for(("f01", "f02")), findings, TierTable())
    assert all(x.excluded for x in out)
    c = conflicts[0]
    assert c.status == "unresolved" and set(c.ids) == {"f01", "f02"} and len(c.claims) == 2 and len(c.sources) == 2


def test_candidate_pairs_are_only_candidates_the_checker_decides():
    findings = [f(1, "Audi won the manufacturers title in 1982", "https://a.example.org/x", page="p1 " * 40),
                f(2, "Audi won the manufacturers title in 1984", "https://b.example.net/y", page="p2 " * 40)]
    assert candidate_pairs(findings)                                  # same words, different years: a candidate
    out, conflicts = weigh(llm_for(), findings, TierTable())          # the checker says: different facts
    assert not any(x.excluded for x in out) and not conflicts


def test_vendor_page_cannot_corroborate_its_own_claims():
    findings = [f(1, "Audi engines produced 500 horsepower", "https://www.audi.com/story", "other", page="v " * 50),
                f(2, "Audi engines produced 500 horsepower", "https://www.carmag.example/review", "journalism", page="w " * 50)]
    out, _ = weigh(llm_for(), findings, TierTable())
    by = {x.id: x for x in out}
    assert by["f01"].self_sourced and by["f01"].independent_origin_count == 1   # only the independent site counts for it
    assert not by["f02"].self_sourced


def test_tier_defaults_and_per_topic_override():
    t = TierTable()
    assert t.tier("https://www.nasa.gov/a") == 4 and t.tier("https://en.wikipedia.org/x") == 2 and t.tier("https://forum.x.com/t") == 0
    assert t.tier("https://random-blog.example/p") == 1 and t.tier("https://www.fia.com/x") == 1
    assert TierTable(override=Path("config/tier_overrides/wrc-group-b.json")).tier("https://www.fia.com/x") == 4


def test_passage_selection_prefers_relevant_text_and_signatures_find_copies():
    text = ("Cooking pasta takes ten minutes in salted water. " * 12 + "The committee banned the class for the 1987 season after fatal crashes in 1986. " * 3
            + "Gardening in spring requires patience and compost. " * 12)
    sel = passages.select(text, "When was the class banned after the fatal crashes?", k=1, size=300)
    assert "banned the class" in sel[0]
    a, b, c = passages.signature(PAGE), passages.signature(PAGE + " small edit"), passages.signature("unrelated " * 200)
    assert passages.similarity(a, b) > 0.5 > passages.similarity(a, c)


def test_wrong_year_finding_is_excluded_from_slides_and_listed_for_review():
    from deckgen.pipeline import review_items
    from deckgen.schemas import DeckSpec, SlideSpec
    from deckgen.schemas.research import Findings

    findings = [
        f(1, "Group A replaced Group B in the 1986 season", "https://blog-one.example.com/post", "other"),
        f(2, "Group A replaced Group B in the 1987 season", "https://history.agency.gov/rules", "official", page="other words " * 40),
    ]
    out, conflicts = weigh(llm_for(("f01", "f02")), findings, TierTable())
    bad = next(x for x in out if x.id == "f01")
    assert bad.excluded                                              # never offered to the slide writer
    spec = DeckSpec(title="t", brand="default_v1", slides=[SlideSpec(id="s01", section="a", layout="TITLE", title="T")])
    items = review_items(spec, {x.id: x for x in out}, {}, Findings(findings=out, conflicts=conflicts))
    assert any("CONFLICT resolved" in i and "1986" in i and "1987" in i and "kept f02" in i for i in items)


def test_research_runs_rounds_until_the_pool_target_and_reports_counts(monkeypatch, tmp_path):
    from deckgen.config import Config
    from deckgen.research import agent, tools as tools_mod
    from deckgen.research.tools import Budget, ResearchTools, SearchResult
    from deckgen.schemas.research import Brief

    monkeypatch.setattr(tools_mod, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(agent, "CONFIG", Config(target_raw_findings=10, max_pages=40))
    counter = {"n": 0}

    class Search:
        def search(self, query, max_results):
            counter["n"] += 1
            return [SearchResult(title="t", url=f"https://site{counter['n']}x{k}.org/p", snippet="s", score=1.0,
                                 content=" ".join(f"w{counter['n']}x{k}y{i}" for i in range(150)) + ". Some page about the topic with enough words to pass the length threshold." * 3) for k in range(3)]

    class NoFetch:
        def fetch(self, url):
            raise AssertionError(url)

    n = {"c": 0}

    def extract(prompt, images):
        n["c"] += 1
        return {"findings": [{"claim": f"Distinct claim number {n['c']}a about the topic", "evidence_snippet": "s", "confidence": "high"},
                             {"claim": f"Distinct claim number {n['c']}b about the topic", "evidence_snippet": "s", "confidence": "medium"}]}

    llm = FakeLLM({"research_query": lambda p, i: {"queries": ["query for " + p.split("Research question: ")[1].split(chr(10))[0]]}, "research_extract": extract, "research_crosscheck": {"links": []},
                   "research_conflict_pairs": {"contradictions": []}})
    tools = ResearchTools(Search(), NoFetch(), Budget(200, 60, 300, 100))
    res = agent.run_research(llm, tools, Brief(topic="t", audience="a", slide_count=8, research_questions=["q1 about the topic", "q2 about the topic"]))
    assert res.stats["raw_findings"] >= 10 and res.stats["rounds"] >= 1
    assert res.stats["findings"] == len(res.findings) and res.stats["origins"] >= 5 and "usable" in res.stats
