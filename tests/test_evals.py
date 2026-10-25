"""Go/no-go logic and eval plumbing, offline (synthetic run metrics; no network, no LLM)."""
import json
from pathlib import Path

from evals.run_evals import comparison_md, gonogo

GOOD = {"slides_built": 12, "requested": 12, "blocking": 0, "unsourced": 0, "fallbacks": 0, "review_items": 6}


def runs(*overrides):
    return [{**GOOD, **o} for o in overrides]


def test_a_topic_that_meets_every_criterion_is_go_and_the_manual_read_stays_pending():
    v = gonogo({"t": runs({}, {"slides_built": 11}, {})})
    assert v["automated_pass"] and v["topics"]["t"]["pass"] and "PENDING" in v["manual"]


def test_each_criterion_can_fail_the_verdict_on_its_own():
    cases = {
        "under 80% on one run": runs({}, {"slides_built": 9}, {}),                    # 9 of 12 = 75%
        "spread of 2": runs({"slides_built": 12}, {"slides_built": 10}, {"slides_built": 12}),
        "blocking issue": runs({}, {"blocking": 1}, {}),
        "blocking unknown (QA not run)": runs({}, {"blocking": None}, {}),
        "unsourced slide": runs({}, {"unsourced": 1}, {}),
        "two fallbacks": runs({}, {"fallbacks": 2}, {}),
        "long review list": runs({}, {"review_items": 40}, {}),
        "a run errored": runs({}, {"error": "boom"}, {}),
    }
    for name, rs in cases.items():
        assert not gonogo({"t": rs})["automated_pass"], name
    assert not gonogo({})["automated_pass"]                                           # nothing run is not a pass


def test_one_failing_topic_fails_the_whole_verdict_and_the_table_names_it():
    by_topic = {"Good topic": runs({}, {}, {}), "Bad topic": runs({"unsourced": 2}, {}, {})}
    v = gonogo(by_topic)
    assert not v["automated_pass"] and v["topics"]["Good topic"]["pass"] and not v["topics"]["Bad topic"]["pass"]
    md = comparison_md(by_topic, v, [{"topic": "Bad topic", "requested": 12, "built": [12, 8]}])
    assert "NO-GO" in md and "GO" in md and "[12, 8]" in md and "Bad topic: fails" in md and "zero unsourced claims" in md


def test_golden_topics_and_baseline_files_are_consistent():
    root = Path(__file__).resolve().parents[1] / "evals"
    topics = json.loads((root / "golden_topics.json").read_text(encoding="utf-8"))
    base = json.loads((root / "baseline.json").read_text(encoding="utf-8"))["rows"]
    assert len(topics) == 7 and any("Baghdad" in t["topic"] for t in topics) and any("pricing" in t["topic"] for t in topics)
    assert {b["topic"] for b in base} <= {t["topic"] for t in topics}                 # every baseline row maps to a golden topic
    assert (Path(__file__).resolve().parents[1] / "config" / "tier_overrides" / f"{topics[0]['tier_overrides']}.json").exists()
