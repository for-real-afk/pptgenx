from pathlib import Path

import pytest

from deckgen.qa import fix_loop, policy
from deckgen.schemas import DeckSpec
from deckgen.schemas.edits import Edit, EditRejected, apply_edits, slide_words
from deckgen.schemas.qa import Issue

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "brand" / "default_v1"
FIXTURE = ROOT / "tests" / "fixtures" / "handwritten_spec.json"


def spec() -> DeckSpec:
    return DeckSpec.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


def iss(slide, type_, sev="high", source="critic"):
    return Issue(slide=slide, type=type_, severity=sev, description="d", suggested_fix="f", source=source)


def test_blocking_policy():
    assert policy.blocking([iss(1, "empty_space", "medium")]) == []
    assert policy.blocking([iss(1, "inconsistent_style", "high")]) == []
    assert len(policy.blocking([iss(1, "overflow", "medium")])) == 1
    assert policy.blocking([iss(2, "tight_spacing", "medium")]) == []                       # critic alone: not blocking
    both = [iss(2, "tight_spacing", "medium"), iss(2, "overlap", "high", "auto")]
    assert len(policy.blocking(both)) == 2                                                  # auto agrees: blocking
    assert policy.blocking([iss(1, "overflow", "low")]) == []


def test_phase_order_overflow_before_overlap():
    phase, hits = policy.next_phase([iss(1, "overlap"), iss(2, "overflow")])
    assert "overflow" in phase and [h.type for h in hits] == ["overflow"]


def test_edits_trim_ok_growth_rejected():
    s = spec().slides[2]
    old = s.composition.items[0].body
    ok = apply_edits(s, [Edit(action="replace", path="/composition/items/0/body", text="Short.")])
    assert ok.composition.items[0].body == "Short." and slide_words(ok) < slide_words(s)
    with pytest.raises(EditRejected):
        apply_edits(s, [Edit(action="replace", path="/composition/items/0/body", text=old + " and many more extra words")])
    with pytest.raises(EditRejected):
        apply_edits(s, [Edit(action="replace", path="/layout", text="X")])                  # structural fields are off-limits
    with pytest.raises(EditRejected):
        apply_edits(s, [Edit(action="delete", path="/composition/items/0"), Edit(action="delete", path="/composition/items/1")])  # min 3 items


class OneEdit:
    def propose(self, slide, issues):
        return [Edit(action="replace", path="/title", text="Short title")]


def test_loop_reverts_when_fix_does_not_help(tmp_path, monkeypatch):
    start = spec()
    calls = []

    def fake_eval(sp, brand_dir, rd, llm, changed, prev, consistency):
        calls.append(changed)
        return [iss(2, "overflow")] if len(calls) == 1 else [iss(2, "overflow"), iss(3, "overlap")]  # candidate is worse

    monkeypatch.setattr(fix_loop, "_evaluate", fake_eval)
    best, rep = fix_loop.run_qa(start, BRAND, tmp_path, llm=None, fixer=OneEdit())
    assert best == start                                   # never worse than it started
    assert rep.rounds[-1].accepted is False and "reverted" in rep.stopped


def test_loop_keeps_improving_fix_and_stops_when_clean(tmp_path, monkeypatch):
    start = spec()
    calls = []

    def fake_eval(sp, brand_dir, rd, llm, changed, prev, consistency):
        calls.append(changed)
        return [iss(2, "overflow")] if len(calls) == 1 else []

    monkeypatch.setattr(fix_loop, "_evaluate", fake_eval)
    best, rep = fix_loop.run_qa(start, BRAND, tmp_path, llm=None, fixer=OneEdit())
    assert best.slides[1].title == "Short title" and best.slides[0] == start.slides[0]
    assert rep.stopped == "no blocking issues" and policy.blocking(rep.remaining) == []
