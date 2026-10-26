"""Offline end-to-end smoke (fake LLM + fake search) and the max-capacity fixture."""
import re
import subprocess
from pathlib import Path

import pytest

from deckgen.llm import FakeLLM, Tracer
from deckgen.pipeline import Request, run_pipeline
from deckgen.qa import checks, soffice
from deckgen.research import tools as tools_mod
from deckgen.research.tools import Budget, ResearchTools, SearchResult
from deckgen.schemas import DeckSpec, SlideSpec
from deckgen.schemas.draft import ItemDraft, SeriesDraft, SlideDraft, draft_to_slide
from deckgen.schemas.qa import CriticReport
from deckgen.schemas.research import (
    ConflictReport,
    Finding,
    Outline,
    OutlineSlide,
    QueryPlan,
    QuestionPlan,
    RawFinding,
    RawFindings,
)
from deckgen.spec import capacity
from deckgen.spec.rules import check_slide

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "brand" / "default_v1"
FACT = "Group B needed 200 road cars in 1982; peaks were 450 hp Peugeot, 480 hp Lancia, 500 hp Audi, banned 1986"


DOMAINS = ["www.wrc.com", "www.topgear.com"]


class Search:
    def search(self, query, max_results):
        return [SearchResult(title=f"Page {i}", url=f"https://{DOMAINS[i]}/p/{query[-1]}{i}", snippet="s", content=" ".join(f"page{query[-1]}{i}w{k}" for k in range(120)) + (" filler text." * 30), score=1.0 - i / 10)
                for i in range(2)]


class NoFetch:
    def fetch(self, url):
        raise AssertionError("no fetch expected")


def extract(prompt, images):
    tag = re.search(r"Question: (question \d)", prompt).group(1) + "|" + re.search(r'source url="[^"]*/(\d+)"', prompt).group(1)
    return RawFindings(findings=[RawFinding(claim=f"[{tag}] {FACT}", evidence_snippet="200 cars 450 480 500 hp", confidence="high")])


def slide_draft(prompt, images):
    n = int(re.search(r"id=s(\d\d)", prompt).group(1))
    base = {"id": f"s{n:02d}", "section": "S", "speaker_notes": "Detail goes here. It has two sentences.",
                "source_refs": {2: ["f01", "f02"], 3: ["f03", "f04"], 4: ["f05", "f06"]}.get(n, ["f01", "f02"])}
    if n == 1:
        return SlideDraft(**{**base, "source_refs": []}, layout="TITLE", title="Group B in brief", subtitle="Rules, cars and the ban")
    if n == 2:
        items = [ItemDraft(icon="FaCarSide", heading=f"Idea {i}", body=f"Rule {i}: builders needed 200 road cars in 1982.") for i in range(3)]
        return SlideDraft(**base, layout="TITLE_ONLY", title="Three ideas shaped the cars", comp_kind="cards", items=items)
    if n == 3 and "DATA SETS FOR A CHART" not in prompt:   # no chart data exists in these findings: the writer must use a plain layout
        items = [ItemDraft(icon="FaCogs", heading=f"Power {i}", body=f"Peaks were 450 hp, 480 hp and 500 hp in item {i}.") for i in range(3)]
        return SlideDraft(**base, layout="TITLE_ONLY", title="Power climbed across the whole field", comp_kind="cards", items=items)
    if n == 3:
        return SlideDraft(**base, layout="CHART_COMMENTARY", title="Power climbed across the whole field",
                          bullets=["Audi led on peak output", "Lancia followed closely"], chart_type="bar", data_ref="d1",
                          chart_title="Approximate peak power (hp)")
    if n == 4:
        items = [ItemDraft(value="200", label="Road cars needed"), ItemDraft(value="1982", label="Group B begins"), ItemDraft(value="1986", label="Class banned")]
        return SlideDraft(**base, layout="TITLE_ONLY", title="Three dates and numbers define the era", comp_kind="stats", items=items)
    return SlideDraft(**base, layout="CLOSING", title="What to remember", bullets=["Rules invited radical cars", "Speed outran safety", "The legacy endures"])


def script():
    return {
        "plan": QuestionPlan(objective="Explain Group B", research_questions=[f"question {i}" for i in range(4)]),
        "research_query": lambda prompt, images: QueryPlan(queries=["group b q" + re.search(r"question (\d)", prompt).group(1)]),
        "research_extract": extract,
        "research_crosscheck": ConflictReport(links=[]),
        "research_conflict_pairs": {"contradictions": []}, "facts_sheet": {"groups": []}, "passage_filter": {"keep": [{"i": i, "slides": [2]} for i in range(30)]}, "spec_deck": lambda prompt, images: (_ for _ in ()).throw(ValueError("no whole-deck script")),
        "outline": Outline(arc="rise-peak-fall-legacy", deck_title="Group B in brief", slides=[
            OutlineSlide(n=1, section="Open", purpose="Title", key_message="Intro", layout_hint="TITLE"),
            OutlineSlide(n=2, section="Open", purpose="Ideas", key_message="Ideas", finding_ids=["f01", "f02"], layout_hint="TITLE_ONLY"),
            OutlineSlide(n=3, section="Cars", purpose="Power", key_message="Power", finding_ids=["f03", "f04"], layout_hint="CHART_COMMENTARY"),
            OutlineSlide(n=4, section="Cars", purpose="Dates", key_message="Dates", finding_ids=["f05", "f06"], layout_hint="TITLE_ONLY"),
            OutlineSlide(n=5, section="End", purpose="Close", key_message="Close", finding_ids=["f01", "f02"], layout_hint="CLOSING")]),
        "spec_slide": slide_draft,
        "faithfulness": {"unsupported": []},
        "deck_consistency": {"problems": []},
        "critic": CriticReport(issues=[]),
        "critic_consistency": CriticReport(issues=[]),
    }


def need_render():
    try:
        soffice.backend()
    except soffice.RenderError:
        pytest.skip("no render backend")


def test_smoke_end_to_end_offline(tmp_path, monkeypatch):
    need_render()
    monkeypatch.setattr(tools_mod, "CACHE_DIR", tmp_path / "cache")
    llm = FakeLLM(script())
    llm.tracer = Tracer(None)
    tools = ResearchTools(Search(), NoFetch(), Budget(60, 30, 120, 60))
    run = tmp_path / "run"
    rep = run_pipeline(Request("Group B", "fans", 6), llm, tools, run, auto_approve=True)
    for f in ("brief.json", "findings.json", "outline.json", "deck_spec.json", "deck_spec.final.json", "deck.pptx", "sources.md", "run_report.md", "qa_report.md"):
        assert (run / f).exists(), f
    assert rep["unsourced_slides"] == [] and rep["invalid_layouts"] == [] and rep["file_errors"] == []
    assert rep["slides"] == 6 and rep["images"] == 6 and rep["qa_blocking_remaining"] == 0
    spec = DeckSpec.model_validate_json((run / "deck_spec.final.json").read_text())
    assert spec.slides[-1].layout == "SOURCES" and ("wrc.com" in spec.slides[-1].placeholders["body"][0].lower() or "page" in spec.slides[-1].placeholders["body"][0].lower())
    # resume: every stage artifact exists, so only the final build/render runs and the LLM is never called again
    calls = len(llm.calls)
    run_pipeline(Request("Group B", "fans", 6), llm, tools, run, auto_approve=True)
    assert len(llm.calls) == calls


def test_ungrounded_chart_value_rejected():
    f = {"f01": Finding(id="f01", question="q", claim="Audi made 500 hp", evidence_snippet="500 hp", source_type="official",
                        source_url="https://x.test/a", retrieved_at="2026-01-01T00:00:00+00:00", confidence="high")}
    s = SlideDraft(id="s03", section="S", layout="CHART_COMMENTARY", title="Power climbed across the whole field", bullets=["Audi led", "Lancia followed"], chart_type="bar",
                   chart_title="Approximate peak power (hp)", categories=["Peugeot", "Lancia", "Audi"], series=[SeriesDraft(name="hp", values=[450, 480, 500])],
                   speaker_notes="Detail goes here. It has two sentences.", source_refs=["f01"])
    errs = check_slide(draft_to_slide(s.model_copy(update={"source_refs": ["f01"]})), f, [])
    assert any("450" in e and "not in the cited findings" in e for e in errs)
    low = s.model_copy(update={"source_refs": ["f01"], "series": [SeriesDraft(name="x", values=[1, 2, 3])]})
    assert any("looks like a rating" in e for e in check_slide(draft_to_slide(low), f, []))


def _words(n_chars: int) -> str:
    return ("word " * (n_chars // 5 + 1))[: max(n_chars - 1, 1)].strip()


def test_every_composition_at_max_capacity_fits(tmp_path):
    """The capacity table must match the renderer: max-length text everywhere -> no overflow/overlap in automated checks."""
    slides = []

    def add(kind, n, make):
        lim = capacity.limits(kind, n)
        slides.append(SlideSpec(id=f"s{len(slides) + 1:02d}", section="Cap", layout="TITLE_ONLY", title=f"Capacity {kind} {n}",
                                composition={"kind": kind, "items": [make(lim, i) for i in range(n)]}))

    for n in (2, 3, 4, 6):
        add("cards", n, lambda lim, i: {"icon": "FaCogs", "heading": _words(lim["heading"]), "body": _words(lim["body"])})
    for n in (2, 3, 4):
        add("stats", n, lambda lim, i: {"value": "9" * lim["value"], "label": _words(lim["label"]), "detail": _words(lim["detail"])})
    for n in (3, 4, 5):
        add("timeline", n, lambda lim, i: {"year": "1" * lim["year"], "heading": _words(lim["heading"]), "body": _words(lim["body"])})
    for n in (2, 3, 4, 5):
        add("icon_rows", n, lambda lim, i: {"icon": "FaCogs", "heading": _words(lim["heading"]), "body": _words(lim["body"])})
    nb = capacity.comparison_max_bullets()
    add("comparison", 2, lambda lim, i: {"heading": _words(lim["heading"]), "bullets": [_words(lim["bullet"])] * nb})
    spec = DeckSpec(title="Capacity", brand="default_v1", slides=slides)
    p = tmp_path / "cap.json"
    p.write_text(spec.model_dump_json(exclude_none=True))
    out = tmp_path / "cap.pptx"
    r = subprocess.run(["node", "renderer/build_deck.js", str(p), str(BRAND), str(out)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    bad = [i for i in checks.check(out) if i.type in ("overflow", "overlap", "off_slide", "margin")]
    assert bad == [], [(i.slide, i.description) for i in bad]
    need_render()
    assert len(soffice.render_deck(out, tmp_path / "r")) == len(slides)


def test_unsupported_statement_is_fixed_by_a_targeted_diff_not_a_rewrite():
    """The fact-checker flags a statement; the fixer proposes a shorter replacement for that one field; the slide is re-checked and kept."""
    from deckgen.schemas.research import Brief
    from deckgen.spec.spec_generator import build_slide

    findings = {f"f0{i}": Finding(id=f"f0{i}", question="q", claim=FACT, evidence_snippet="200 cars 450 480 500 hp", source_type="official",
                                  source_url=f"https://site{i}.test/x", retrieved_at="2026-01-01T00:00:00+00:00", confidence="high") for i in (1, 2)}
    verdicts = iter([{"issues": [{"claim_quote": "Audi won in 1983", "kind": "unsupported"}]}, {"issues": []}])
    sc = script()
    sc["faithfulness"] = lambda prompt, images: next(verdicts)

    def wrong_year(prompt, images):  # item 0 states a year no source passage or finding contains: code can confirm the checker's quote
        d = slide_draft(prompt, images)
        d.items[0].body = "Audi won in 1983 with 200 cars."
        return d

    sc["spec_slide"] = wrong_year
    sc["fixer"] = {"edits": [{"action": "replace", "path": "/composition/items/0/body", "text": "Rule 0: builders needed 200 road cars."}], "cite": []}
    llm = FakeLLM(sc)
    brief = Brief(topic="t", audience="a", slide_count=6)
    slide = build_slide(llm, brief, sc["outline"].slides[1], sc["outline"], findings, BRAND, [], False)
    stages = [c["stage"] for c in llm.calls]
    assert stages.count("spec_slide") == 1 and stages.count("fixer") == 1          # written once, fixed once
    assert slide.composition.items[0].body == "Rule 0: builders needed 200 road cars."
    assert slide.composition.items[1].body == "Rule 1: builders needed 200 road cars in 1982."  # other fields untouched
    assert "Audi won in 1983" in next(c["prompt"] for c in llm.calls if c["stage"] == "fixer")


def test_exhausted_fixes_fall_back_to_grounded_slide():
    from deckgen.schemas.research import Brief
    from deckgen.spec.spec_generator import build_slide

    fs = {f"f0{i}": Finding(id=f"f0{i}", question="q", claim=f"Group B rule number {i} limited weight to 1000 kg",
                            evidence_snippet="weight limit", source_type="official", source_url=f"https://site{i}.test/a",
                            retrieved_at="2026-01-01T00:00:00+00:00", confidence="high") for i in (1, 2, 3)}

    def invents(prompt, images):
        items = [ItemDraft(icon="FaBook", heading="Rule", body="Ferdinand Piech led the response.") for _ in range(3)]
        return SlideDraft(id="s02", section="S", layout="TITLE_ONLY", title="Invented names appear here", comp_kind="icon_rows",
                          items=items, speaker_notes="n", source_refs=["f01", "f02", "f03"])

    sc = script()
    sc["spec_slide"] = invents
    sc["fixer"] = {"edits": [], "cite": []}
    sc["spec_fallback_labels"] = {"labels": ["Weight", "Weight", "Weight"]}
    llm = FakeLLM(sc)
    llm.tracer = Tracer(None)
    o = sc["outline"].slides[1].model_copy(update={"finding_ids": ["f01", "f02", "f03"], "key_message": "Group B capped car weight"})
    s = build_slide(llm, Brief(topic="t", audience="a", slide_count=6), o, sc["outline"], fs, BRAND, [], False)
    assert llm.tracer.attempts["spec_slide_fallback"] == [2]
    assert [i.body for i in s.composition.items] == [f.claim for f in fs.values()]  # verbatim claims: grounded by construction


def test_outline_repair_drops_unsupported_slides_and_renumbers():
    from deckgen.planning.outliner import repair_outline

    o = Outline(arc="timeline", deck_title="D", slides=[
        OutlineSlide(n=1, section="A", purpose="p", key_message="title", layout_hint="TITLE"),
        OutlineSlide(n=2, section="A", purpose="p", key_message="one", finding_ids=["f01", "f02"], layout_hint="TITLE_ONLY"),
        OutlineSlide(n=3, section="A", purpose="p", key_message="thin", finding_ids=["f03"], layout_hint="TITLE_ONLY"),
        OutlineSlide(n=4, section="B", purpose="p", key_message="same evidence", finding_ids=["f01", "f02", "f04"], layout_hint="TITLE_ONLY"),
        OutlineSlide(n=5, section="B", purpose="p", key_message="two", finding_ids=["f05", "f06"], layout_hint="TITLE_ONLY"),
        OutlineSlide(n=6, section="B", purpose="p", key_message="close", finding_ids=["f01"], layout_hint="CLOSING")])
    fixed, dropped = repair_outline(o)
    assert [s.key_message for s in fixed.slides] == ["title", "one", "two", "close"] and [s.n for s in fixed.slides] == [1, 2, 3, 4]
    assert len(dropped) == 2


def test_finished_run_is_copied_to_the_outputs_folder(tmp_path, monkeypatch):
    need_render()
    monkeypatch.setattr(tools_mod, "CACHE_DIR", tmp_path / "cache")
    llm = FakeLLM(script())
    llm.tracer = Tracer(None)
    rep = run_pipeline(Request("Group B", "fans", 6), llm, ResearchTools(Search(), NoFetch(), Budget(60, 30, 120, 60)), tmp_path / "myrun", auto_approve=True)
    dest = Path(rep["exported_to"])
    assert dest == tmp_path / "outputs" / "myrun"
    assert (dest / "deck.pptx").exists() and (dest / "sources.md").exists() and (dest / "review_before_use.md").exists()
    assert len(list((dest / "images").glob("slide-*.jpg"))) == 6
