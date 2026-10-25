"""Seeded-bad-slide tests: the claim checks must FLAG every known-bad edit of a known-good slide.
A check that has never failed on a bad slide proves nothing; these are the known-bad slides."""
import pytest
from pathlib import Path

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


def test_verbatim_copy_is_a_warning_when_short_and_a_hard_gate_when_long():
    from deckgen.spec.claims import claim_violations

    short = "builders needed 200 road cars in 1982 under the new rules"                       # 11 words
    long_ = short + " and every one of them had to be sold to the public in showrooms"           # 22 words
    f = lambda snip: {**FINDINGS, "f01": FINDINGS["f01"].model_copy(update={"evidence_snippet": snip})}
    vs = claim_violations(bad(**{"composition.items.0.body": short.capitalize()}), f(short))
    assert [(x.check, x.level) for x in vs if x.check.startswith("copy")] == [("copy_short", "warn")]
    vs = claim_violations(bad(**{"composition.items.0.body": long_.capitalize()}), f(long_))
    assert ("copy_long", "hard") in [(x.check, x.level) for x in vs]


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


# ---- slide quality: specificity, attribution, title support, timeline labels, redundancy deletion, title slide ----
def test_filler_cards_are_flagged_and_concrete_ones_pass():
    filler = bad(**{"composition.items.0.body": "The drive architecture established a lasting competitive standard.",
                    "composition.items.1.body": "Rivals were forced to adopt similar technology quickly.",
                    "composition.items.2.body": "This changed the entire competitive landscape for good."})
    assert any("no number, date or name" in e for e in claim_errors(filler, FINDINGS))
    assert not any("no number, date or name" in e for e in claim_errors(slide(), FINDINGS))


def test_single_source_strong_claim_needs_attribution():
    shaky = {**FINDINGS, "f02": FINDINGS["f02"].model_copy(update={"needs_review": True, "review_reason": "single source"})}
    assert any("attribute" in e for e in claim_errors(slide(), shaky))
    attributed = bad(**{"composition.items.2.body": "According to WRC.com, Hannu Mikkola won the 1983 drivers title."})
    assert not any("attribute" in e for e in claim_errors(attributed, shaky))


def test_title_numbers_must_be_supported():
    assert any("title says 'hundred'" in e for e in claim_errors(bad(title="Over five hundred horsepower defined Group B"), FINDINGS))
    assert any("title number" in e for e in claim_errors(bad(title="Group B demanded 2000 cars"), FINDINGS))


def test_timeline_label_must_be_a_date():
    s = slide(composition={"kind": "timeline", "items": [
        {"year": "Intro", "heading": "Start", "body": "Group B began in 1982."},
        {"year": "1983", "heading": "Title", "body": "Hannu Mikkola won in 1983."},
        {"year": "1986", "heading": "End", "body": "Group B ended in 1986."}]}, source_refs=["f01", "f02"])
    assert any("must be a year or date" in e for e in claim_errors(s, {**FINDINGS, "f01": FINDINGS["f01"].model_copy(
        update={"claim": FINDINGS["f01"].claim + " ended in 1986 and began 1982"})}))


def test_redundant_slides_are_deleted_not_rewritten(tmp_path):
    from deckgen.schemas.research import Outline, OutlineSlide
    from deckgen.spec.spec_generator import _repair_deck

    vocab = "amber basil cedar delta ember fjord glint haven ivory jaded kappa lunar mango noble ocean pearl quartz raven sable tulip umber vivid wheat xenon yarrow zephyr".split()

    def mk(i, refs):
        items = [{"icon": "FaBook", "heading": f"Head {k}", "body": f"{vocab[(i * 4 + k) % 26]} {vocab[(i * 4 + k + 7) % 26]} {vocab[(i * 4 + k + 13) % 26]} {vocab[(i * 4 + k + 19) % 26]} 1982"} for k in range(3)]
        return slide(id=f"s{i:02d}", source_refs=refs, title=f"Distinct message number {i} here", composition={"kind": "icon_rows", "items": items})

    slides = [slide(id="s01", layout="TITLE", title="T", composition=None, source_refs=[])] + \
             [mk(i, r) for i, r in zip(range(2, 8), (["f01", "f02"], ["f01", "f02"], ["f03"], ["f04"], ["f05"], ["f06"]))] + \
             [slide(id="s08", layout="CLOSING", title="Next", composition=None, placeholders={"body": ["Watch the rules"]}, source_refs=["f01"])]
    outline = Outline(arc="timeline", deck_title="D", slides=[OutlineSlide(n=i, section="S", purpose="p", key_message="k") for i in range(1, 9)])
    llm = FakeLLM({"deck_consistency": {"problems": []}})
    by_id = {f"f0{i}": FINDINGS["f01"].model_copy(update={"id": f"f0{i}"}) for i in range(1, 7)}
    out = _repair_deck(llm, Brief(topic="t", audience="a", slide_count=9), outline, by_id, Path("brand/default_v1"), slides, tmp_path)
    assert len(out) == len(slides) - 1 and [s.id for s in out] == [f"s{k:02d}" for k in range(1, len(out) + 1)]
    notes = __import__("json").loads((tmp_path / "deck_notes.json").read_text())
    assert notes["deleted_slides"] == [3]                      # the later of the two slides sharing f01+f02
    assert out[-1].layout == "CLOSING" and out[0].layout == "TITLE"


def test_title_slide_comes_from_the_request_not_findings():
    from deckgen.schemas.research import Outline, OutlineSlide
    from deckgen.spec.spec_generator import title_slide

    brief = Brief(topic="The golden era of WRC Group B (1980s)", audience="motorsport enthusiasts", slide_count=14,
                  objective="Show how Group B rose and why it ended")
    o = Outline(arc="timeline", deck_title="Group B", slides=[OutlineSlide(n=i, section="S", purpose="p", key_message="k") for i in (1, 2, 3)])
    s = title_slide(brief, o, o.slides[0])
    assert s.title == brief.topic and s.placeholders["subtitle"] == brief.objective and s.source_refs == []


# ---- gate profiles: hard gates vs warnings ----------------------------------------------------------------------------------
def test_registry_levels_follow_the_profile_and_every_check_has_a_category():
    from deckgen import registry

    assert registry.level("copy_short", "strict") == "hard" and registry.level("copy_short", "demoted") == "warn"
    assert registry.level("overclaim_word", "demoted") == "hard" and registry.level("deck_contradiction", "demoted") == "hard"
    assert registry.level("composition_capacity", "demoted") == "warn" and registry.level("numbers_need_two_origins_text", "demoted") == "warn"
    assert registry.level("numbers_need_two_origins_stat", "demoted") == "hard"
    assert all(c.category in ("correctness", "model_compensation") for c in registry.CHECKS)
    from deckgen.spec import claims, rules, validate_spec, faithfulness
    import re

    used = set()
    for mod in (claims, rules, validate_spec, faithfulness):
        used |= set(re.findall(r'v\("([a-z_]+)"', Path(mod.__file__).read_text(encoding="utf-8")))
    assert used <= set(registry.BY_ID), f"checks used in code but missing from the registry: {used - set(registry.BY_ID)}"


def test_slide_failing_only_a_formatting_rule_is_kept_with_a_warning_not_dropped(tmp_path):
    from deckgen.schemas.draft import ItemDraft, SlideDraft
    from deckgen.schemas.research import Brief, Outline, OutlineSlide
    from deckgen.spec.spec_generator import generate_slide

    long_body = "Builders needed 200 road cars in 1982 " + "and the rest of the rules were detailed at length " * 8
    draft = SlideDraft(id="s02", section="S", layout="TITLE_ONLY", title="Group B rules set a low production bar", comp_kind="icon_rows",
                       items=[ItemDraft(icon="FaBook", heading="Homologation", body=long_body),
                              ItemDraft(icon="FaCarSide", heading="Four-wheel drive", body="Audi Sport introduced the Quattro."),
                              ItemDraft(icon="FaTrophy", heading="Title", body="Hannu Mikkola won the 1983 drivers title.")],
                       speaker_notes="n", source_refs=["f01", "f02"])
    sc = {"spec_slide": draft, "faithfulness": {"unsupported": [], "vague": []}}
    o = OutlineSlide(n=2, section="S", purpose="p", key_message="k", finding_ids=["f01", "f02"], layout_hint="TITLE_ONLY")
    outline = Outline(arc="timeline", deck_title="D", slides=[o, o.model_copy(update={"n": 3}), o.model_copy(update={"n": 4})])
    notes: dict = {}
    s = generate_slide(FakeLLM(sc), Brief(topic="t", audience="a", slide_count=6), o, outline, FINDINGS, Path("brand/default_v1"), [], False, None, None, notes)
    assert s.composition.items[0].body == long_body                                  # kept as written
    assert any(w["check"] == "composition_capacity" for w in notes["warnings"])      # and reported


def test_slide_the_evidence_cannot_support_fails_with_category_evidence():
    from deckgen.schemas.draft import ItemDraft, SlideDraft
    from deckgen.schemas.research import Brief, Outline, OutlineSlide
    from deckgen.spec.spec_generator import SpecGenerationError, generate_slide

    invented = SlideDraft(id="s02", section="S", layout="TITLE_ONLY", title="Group B rules set a low production bar", comp_kind="icon_rows",
                          items=[ItemDraft(icon="FaBook", heading="H", body="Builders needed 9,000 road cars in 1982.") for _ in range(3)],
                          speaker_notes="n", source_refs=["f01", "f02"])
    llm = FakeLLM({"spec_slide": invented, "faithfulness": {"unsupported": [], "vague": []}, "spec_fallback_labels": {"labels": ["a", "b", "c"]}})
    o = OutlineSlide(n=2, section="S", purpose="p", key_message="k", finding_ids=["f01", "f02"], layout_hint="TITLE_ONLY")
    outline = Outline(arc="timeline", deck_title="D", slides=[o, o.model_copy(update={"n": 3}), o.model_copy(update={"n": 4})])
    try:
        generate_slide(llm, Brief(topic="t", audience="a", slide_count=6), o, outline, FINDINGS, Path("brand/default_v1"), [], False)
        raise AssertionError("an unsupported number must not produce a slide")
    except SpecGenerationError as e:
        assert e.category == "evidence" and any(v.check == "number_in_text_grounded" for v in e.violations)
