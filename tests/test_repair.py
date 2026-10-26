"""Facts sheet and cross-slide repair pass, offline with scripted fakes."""
from pathlib import Path

from deckgen.llm import FakeLLM
from deckgen.research.facts import Group, Member, Sheet, decide
from deckgen.schemas import DeckSpec
from deckgen.schemas.research import Brief, Finding, Findings, Outline, OutlineSlide
from deckgen.spec.repair import repair_deck

from test_claims import FINDINGS, slide


def f(i, claim, url, tier=1, origins=1, conf="medium"):
    return Finding(id=f"f{i:02d}", question="q", claim=claim, evidence_snippet="snippet text", source_type="journalism", source_url=url,
                   retrieved_at="2026-01-01T00:00:00+00:00", confidence=conf, tier=tier, independent_origin_count=origins)


def test_facts_sheet_picks_by_weight_or_says_sources_differ():
    by_id = {"f01": f(1, "Audi left after Corsica", "https://www.wrc.com/a", tier=4, origins=3, conf="high"),
             "f02": f(2, "Audi left after Portugal", "https://blog.test/b", tier=1),
             "f03": f(3, "Peak power 490 hp", "https://a.test/c", tier=2), "f04": f(4, "Peak power 550 hp", "https://b.test/d", tier=2)}
    won = decide(Group(subject="Audi", attribute="withdrawal timing", members=[Member(id="f01", value="after Corsica 1986"), Member(id="f02", value="after Portugal 1985")]), by_id)
    assert won.status == "resolved" and "Corsica" in won.value and won.source == "wrc.com" and "Portugal" not in won.value
    tie = decide(Group(subject="Lancia Delta S4", attribute="competition power", members=[Member(id="f03", value="490 hp"), Member(id="f04", value="550 hp")]), by_id)
    assert tie.status == "differ" and tie.value.startswith("sources differ: 490 hp (a.test) vs 550 hp (b.test)")
    assert decide(Group(subject="x", attribute="y", members=[Member(id="f03", value="490 hp"), Member(id="f04", value="490 HP")]), by_id) is None   # they agree
    text = Sheet(entries=[won, tie], single_source=["f02 (blog.test)"]).text()
    assert "[USE]" in text and "[SOURCES DIFFER]" in text and "[HEDGE]" in text


def _deck():
    spec = DeckSpec(title="D", brand="default_v1", slides=[
        slide(id="s01", layout="TITLE", title="T", composition=None, source_refs=[]),
        slide(id="s02", title="Audi left after Portugal in 1985 here"), slide(id="s03", title="Audi left after Corsica in 1986 here"),
        slide(id="s04", layout="CLOSING", title="Next", composition=None, placeholders={"body": ["Watch the rules"]}, source_refs=["f01"])])
    outline = Outline(arc="x", deck_title="D", slides=[OutlineSlide(n=i, section="S", purpose="p", key_message="k") for i in range(1, 5)])
    return spec, outline, Brief(topic="t", audience="a", slide_count=5), Findings(findings=list(FINDINGS.values()))


def test_repair_stops_when_no_contradiction_is_left():
    spec, outline, brief, findings = _deck()
    q = [{"slide": 2, "text": "Audi left after Portugal in 1985 here"}, {"slide": 3, "text": "Audi left after Corsica in 1986 here"}]
    problems = iter([{"problems": [{"kind": "contradiction", "slides": [2, 3], "description": "different accounts of when Audi left", "quotes": q}]}, {"problems": []}])
    fixer = {"edits": [{"action": "replace", "path": "/title", "text": "Sources differ on Audi exit"}], "cite": []}
    llm = FakeLLM({"deck_consistency": lambda prompt, images: next(problems), "fixer": fixer})
    out, rep = repair_deck(llm, spec, brief, outline, findings, Path("brand/default_v1"), "FACTS SHEET: x")
    assert rep["before"].get("contradiction") == 1 and rep["after"].get("contradiction", 0) == 0 and len(rep["rounds"]) == 1   # nothing left, so no second round
    assert "Sources differ on Audi exit" in (out.slides[1].title, out.slides[2].title)
    assert any(e["result"] == "edited" for e in rep["rounds"][0]["edits"])


def test_repair_gives_up_after_two_rounds():
    spec, outline, brief, findings = _deck()
    q = [{"slide": 2, "text": "Audi left after Portugal in 1985 here"}]
    always = {"problems": [{"kind": "contradiction", "slides": [2, 3], "description": "still inconsistent", "quotes": q}]}
    llm = FakeLLM({"deck_consistency": always, "fixer": {"edits": [], "cite": []}})
    _, rep = repair_deck(llm, spec, brief, outline, findings, Path("brand/default_v1"), "")
    assert len(rep["rounds"]) == 2 and rep["after"].get("contradiction") == 1


def test_merge_groups_joins_the_same_subject_and_attribute():
    from deckgen.research.facts import merge_groups

    a = Group(subject="Audi", attribute="withdrawal timing", members=[Member(id="f01", value="after Corsica")])
    b = Group(subject="Audi", attribute="timing of withdrawal", members=[Member(id="f02", value="after Portugal"), Member(id="f01", value="after Corsica")])
    c = Group(subject="Lancia Delta S4", attribute="power", members=[Member(id="f03", value="490 hp")])
    merged = merge_groups([a, b, c])
    assert len(merged) == 2 and sorted(m.id for m in merged[0].members) == ["f01", "f02"]


def test_deterministic_check_fails_when_two_slides_state_different_values():
    from deckgen.research.facts import Entry, Sheet, facts_problems

    sheet = Sheet(entries=[Entry(subject="Audi", attribute="withdrawal timing", status="resolved", value="after Corsica 1986", tier=4, source="wrc.com",
                                 values=["after Corsica 1986", "after Portugal 1985"], ids=["f01", "f02"])])

    def deck(*titles):
        return DeckSpec(title="D", brand="default_v1", slides=[slide(id="s01", layout="TITLE", title="T", composition=None, source_refs=[])] +
                        [slide(id=f"s{i:02d}", title=t, composition=None, placeholders={"body": [t]}) for i, t in enumerate(titles, 2)])

    bad = facts_problems(deck("Audi withdrew after Portugal 1985", "Audi withdrew after Corsica 1986"), sheet)
    assert any(p.slides == [2, 3] for p in bad) and any(p.slides == [2] for p in bad)      # different values on two slides; slide 2 also disagrees with the sheet
    assert facts_problems(deck("Audi withdrew after Corsica 1986", "Audi left after Corsica 1986 too"), sheet) == []


def test_chart_data_is_filled_by_code_from_the_named_data_set():
    import pytest

    from deckgen.research.datasets import DataSets
    from deckgen.research.facts import Entry, Sheet
    from deckgen.schemas.draft import SlideDraft

    fs = [f(1, "Peugeot 205 T16 produced 450 hp in Evo 2 trim", "https://www.wrc.com/a", tier=4), f(2, "Lancia Delta S4 produced 490 hp in competition trim", "https://a.test/b"),
          f(3, "Ford RS200 produced 450 hp in competition form", "https://c.test/c"), f(4, "Audi Quattro S1 produced 500 hp at peak", "https://d.test/d")]
    sheet = Sheet(entries=[Entry(subject="Lancia Delta S4", attribute="power", status="differ", value="sources differ: 490 hp vs 550 hp", ids=["f02"])])
    ds = DataSets(fs, sheet)
    assert ds.offered and "hp" in ds.listing([])
    d = SlideDraft(id="s02", section="x", layout="CHART_COMMENTARY", title="Power", chart_type="column", chart_title="Peak power", data_ref="d1", bullets=["a"])
    out = ds.fill(d)
    assert "Lancia Delta S4" not in out.categories and len(out.categories) == 3 and out.series[0].values and "sources differ" in out.speaker_notes   # tied item left out, noted
    assert out.source_refs and all(r in {"f01", "f03", "f04"} for r in out.source_refs)
    with pytest.raises(ValueError, match="data_ref"):
        ds.fill(d.model_copy(update={"data_ref": ""}))


def test_whole_deck_call_returns_first_versions_and_failures_fall_back():
    from deckgen.schemas.draft import ItemDraft, SlideDraft
    from deckgen.spec.deck_writer import DeckDraft, write_deck

    spec, outline, brief, findings = _deck()
    pool = {x.id: x for x in findings.findings}
    good = SlideDraft(id="s02", section="Rules", layout="CLOSING", title="Next", bullets=["Watch the rules"], source_refs=["f01"], speaker_notes="n")
    bad = SlideDraft(id="s03", section="Rules", layout="TITLE_ONLY", title="One card", comp_kind="cards", items=[ItemDraft(icon="FaBook", heading="a", body="b")])   # 1 card: cannot become a slide
    llm = FakeLLM({"spec_deck": DeckDraft(slides=[good, bad])})
    got = write_deck(llm, brief, outline, pool, Path("brand/default_v1"), {}, "FACTS SHEET: x")
    assert set(got) == {2} and "FACTS SHEET: x" in llm.calls[0]["prompt"] and "APPROVED OUTLINE" in llm.calls[0]["prompt"]
