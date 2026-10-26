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
    won = decide(Group(topic="when Audi left", members=[Member(id="f01", value="after Corsica 1986"), Member(id="f02", value="after Portugal 1985")]), by_id)
    assert won.status == "chosen" and "Corsica" in won.wording and "wrc.com" in won.wording and "Portugal" not in won.wording
    tie = decide(Group(topic="S4 power", members=[Member(id="f03", value="490 hp"), Member(id="f04", value="550 hp")]), by_id)
    assert tie.status == "differ" and tie.wording.startswith("sources differ: 490 hp (a.test) vs 550 hp (b.test)")
    assert decide(Group(topic="x", members=[Member(id="f03", value="490 hp"), Member(id="f04", value="490 HP")]), by_id) is None   # they agree
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
    problems = iter([{"problems": [{"kind": "contradiction", "slides": [2, 3], "description": "different accounts of when Audi left"}]}, {"problems": []}])
    fixer = {"edits": [{"action": "replace", "path": "/title", "text": "Sources differ on Audi exit"}], "cite": []}
    llm = FakeLLM({"deck_consistency": lambda prompt, images: next(problems), "fixer": fixer})
    out, rep = repair_deck(llm, spec, brief, outline, findings, Path("brand/default_v1"), "FACTS SHEET: x")
    assert rep["before"].get("contradiction") == 1 and rep["after"].get("contradiction", 0) == 0 and len(rep["rounds"]) == 1   # nothing left, so no second round
    assert "Sources differ on Audi exit" in (out.slides[1].title, out.slides[2].title)
    assert any(e["result"] == "edited" for e in rep["rounds"][0]["edits"])


def test_repair_gives_up_after_two_rounds():
    spec, outline, brief, findings = _deck()
    always = {"problems": [{"kind": "contradiction", "slides": [2, 3], "description": "still inconsistent"}]}
    llm = FakeLLM({"deck_consistency": always, "fixer": {"edits": [], "cite": []}})
    _, rep = repair_deck(llm, spec, brief, outline, findings, Path("brand/default_v1"), "")
    assert len(rep["rounds"]) == 2 and rep["after"].get("contradiction") == 1
