"""Seeded-bad-slide tests: the claim checks must FLAG every known-bad edit of a known-good slide.
A check that has never failed on a bad slide proves nothing; these are the known-bad slides."""
import pytest

from deckgen.llm import FakeLLM
from deckgen.schemas import DeckSpec, SlideSpec
from deckgen.schemas.research import Brief, Finding
from deckgen.spec.claims import claim_errors
from deckgen.spec.deck_checks import consistency, coverage, redundancy
from deckgen.spec.rules import check_slide


def finding(i, claim, url, typ="journalism", snippet="short quote"):
    return Finding(id=f"f0{i}", question="rules", claim=claim, evidence_snippet=snippet, source_type=typ, source_url=url,
                   retrieved_at="2026-01-01T00:00:00+00:00", confidence="high")


FINDINGS = {
    "f01": finding(1, "Group B required manufacturers to build 200 road cars in 1982 under FIA rules", "https://www.topgear.com/a",
                   snippet="200 road cars were needed for homologation"),
    "f02": finding(2, "Audi Sport introduced the Quattro with four-wheel drive and Hannu Mikkola won the 1983 drivers title", "https://www.wrc.com/b", "official"),
}


def slide(**kw) -> SlideSpec:
    base = dict(id="s02", section="Rules", layout="TITLE_ONLY", title="Group B rules set a low production bar",
                composition={"kind": "icon_rows", "items": [
                    {"icon": "FaBook", "heading": "Homologation", "body": "Builders needed 200 road cars in 1982."},
                    {"icon": "FaCarSide", "heading": "Four-wheel drive", "body": "Audi Sport introduced the Quattro."},
                    {"icon": "FaTrophy", "heading": "Title", "body": "Hannu Mikkola won the 1983 drivers title."}]},
                speaker_notes="Notes.", source_refs=["f01", "f02"])
    base.update(kw)
    return SlideSpec(**base)


def bad(**changes) -> SlideSpec:
    d = slide().model_dump(exclude_none=True)
    for path, val in changes.items():
        keys = path.split(".")
        cur = d
        for k in keys[:-1]:
            cur = cur[int(k)] if k.isdigit() else cur[k]
        cur[int(keys[-1]) if keys[-1].isdigit() else keys[-1]] = val
    return SlideSpec.model_validate(d)


def test_known_good_slide_passes_claim_checks():
    assert claim_errors(slide(), FINDINGS) == []
    assert check_slide(slide(), FINDINGS, []) == []


SEEDS = {
    "number changed 200 -> 2,000": bad(**{"composition.items.0.body": "Builders needed 2,000 road cars in 1982."}),
    "year swapped 1983 -> 1984": bad(**{"composition.items.2.body": "Hannu Mikkola won the 1984 drivers title."}),
    "name swapped": bad(**{"composition.items.2.body": "Stig Blomqvist won the 1983 drivers title."}),
    "unsupported 'first'": bad(**{"composition.items.1.body": "Audi Sport introduced the first Quattro."}),
    "'exponential' rhetoric": bad(title="Group B power grew exponential beyond the rules"),
    "derived percentage '4%'": bad(**{"composition.items.0.body": "Builders needed 4% of the usual run in 1982."}),
    "'all' overgeneralization": bad(**{"composition.items.0.body": "All builders needed 200 road cars in 1982."}),
    "typo 'increasement'": bad(**{"composition.items.0.body": "Builders saw an increasement of 200 road cars in 1982."}),
}


@pytest.mark.parametrize("name", SEEDS)
def test_seeded_bad_claim_is_flagged(name):
    assert check_slide(SEEDS[name], FINDINGS, []), f"seed not caught: {name}"


def test_verbatim_copy_flagged():
    f = {**FINDINGS, "f01": FINDINGS["f01"].model_copy(update={"evidence_snippet": "builders needed 200 road cars in 1982 under the new rules"})}
    s = bad(**{"composition.items.0.body": "Builders needed 200 road cars in 1982 under the new rules"})
    assert any("copies source wording" in e for e in claim_errors(s, f))


def test_vendor_only_numbers_rejected_but_two_sources_pass():
    one = {k: v.model_copy(update={"source_url": "https://www.audi.com/x", "source_type": "manufacturer"}) for k, v in FINDINGS.items()}
    assert any("independent" in e for e in claim_errors(slide(), one))
    assert not any("independent" in e for e in claim_errors(slide(), FINDINGS))
    official = {k: v.model_copy(update={"source_url": "https://www.fia.com/x", "source_type": "official"}) for k, v in one.items()}
    assert not any("independent" in e for e in claim_errors(slide(), official))


def test_redundancy_detected():
    title = slide(id="s01", layout="TITLE", title="T", composition=None, source_refs=[])
    spec = DeckSpec(title="t", brand="default_v1", slides=[title, slide(id="s02"), slide(id="s03", title="Another message title here")])
    found = redundancy(spec)
    assert found and found[0].kind == "redundancy"


def test_consistency_pass_wiring():
    spec = DeckSpec(title="t", brand="default_v1", slides=[slide()])
    llm = FakeLLM({"deck_consistency": {"problems": [{"kind": "contradiction", "slides": [2, 3], "description": "power differs"}]}})
    probs = consistency(llm, spec, FINDINGS)
    assert probs[0].kind == "contradiction" and "SLIDE 1" in llm.calls[0]["prompt"]


def test_coverage_names_dropped_questions_with_reasons():
    brief = Brief(topic="t", audience="a", slide_count=6, research_questions=["rules", "machines", "legacy"])
    findings = [FINDINGS["f01"], FINDINGS["f02"].model_copy(update={"question": "legacy", "confidence": "low"})]
    spec = DeckSpec(title="t", brand="default_v1", slides=[slide(source_refs=["f01"])])
    cov = coverage(brief, findings, spec)
    assert cov["rules"].startswith("covered") and "no usable findings" in cov["machines"] and "low-confidence" in cov["legacy"]


import os

LIVE = pytest.mark.skipif(not os.environ.get("DECKGEN_LIVE") or not os.environ.get("GEMINI_API_KEY"), reason="live LLM test")
LLM_SEEDS = {
    "number": bad(**{"composition.items.0.body": "Builders needed 2,000 road cars in 1982."}),
    "year": bad(**{"composition.items.2.body": "Hannu Mikkola won the 1984 drivers title."}),
    "name": bad(**{"composition.items.2.body": "Stig Blomqvist won the 1983 drivers title."}),
    "first": bad(**{"composition.items.1.body": "Audi Sport introduced the first Quattro."}),
    "invented cause": bad(**{"composition.items.1.body": "Audi Sport introduced the Quattro because rivals cheated."}),
}


@LIVE
def test_live_fact_checker_flags_every_seed_and_passes_the_good_slide():
    from deckgen.llm import GeminiLLM
    from deckgen.spec.faithfulness import unsupported_statements

    llm = GeminiLLM()
    cited = [FINDINGS["f01"], FINDINGS["f02"]]
    assert unsupported_statements(llm, slide(), cited) == [], "false positive on the known-good slide"
    missed = [k for k, s in LLM_SEEDS.items() if not unsupported_statements(llm, s, cited)]
    assert missed == [], f"fact-checker missed: {missed}"
