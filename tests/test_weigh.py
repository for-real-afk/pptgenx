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


def test_equal_weight_conflict_is_recorded_with_both_values_and_both_excluded(monkeypatch):
    from deckgen.config import Config

    monkeypatch.setattr("deckgen.research.weigh.CONFIG", Config(hedge_contested=False))  # the drop-both behaviour
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


# ---- step 5: data shapes -> table / chart -------------------------------------------------------------------------------------
def test_shape_detector_finds_a_chart_and_a_table_and_ignores_unrelated_findings():
    from deckgen.research.shapes import attach, detect
    from deckgen.schemas.research import OutlineSlide

    def g(i, claim, q="q1"):
        x = f(i, claim, f"https://s{i}.example.org/p", page=f"unique page {i} " * 30)
        return x.model_copy(update={"question": q})

    power = [g(1, "Lancia Delta S4 produced 493 bhp at peak"), g(2, "Peugeot 205 T16 produced 550 bhp at peak"), g(3, "Audi Sport Quattro produced 450 bhp at peak")]
    dated = [g(10 + i, f"The championship was won by driver number {i} in {1980 + i}", "q2") for i in range(5)]
    noise = [g(20, "The weather was cloudy on the day"), g(21, "Spectators gathered along the road")]
    hints = detect(power + dated + noise)
    kinds = {h.kind: h for h in hints}
    assert set(kinds) == {"chart", "table"}
    assert kinds["chart"].unit == "bhp" and [r.value for r in kinds["chart"].rows] == ["493", "550", "450"]
    assert [r.label for r in kinds["table"].rows] == ["1980", "1981", "1982", "1983", "1984"]
    assert detect(noise) == []
    slides = [OutlineSlide(n=2, section="s", purpose="p", key_message="k", finding_ids=["f01", "f02"], layout_hint="CHART_COMMENTARY"),
              OutlineSlide(n=3, section="s", purpose="p", key_message="k", finding_ids=["f10"], layout_hint="TABLE"),
              OutlineSlide(n=4, section="s", purpose="p", key_message="k", finding_ids=["f20"], layout_hint="TITLE_ONLY")]
    out = attach(slides, hints)
    assert "native chart candidate" in out[0].data_hint and "493" in out[0].data_hint
    assert "table candidate" in out[1].data_hint and out[2].data_hint == ""


def test_chart_written_from_a_hint_is_grounded_and_reaches_the_writer_prompt(tmp_path):
    from deckgen.research.shapes import detect
    from deckgen.schemas.draft import SeriesDraft, SlideDraft
    from deckgen.schemas.research import Brief, Outline, OutlineSlide
    from deckgen.spec.spec_generator import build_slide

    fs = {}
    for i, (n, v) in enumerate([("Lancia Delta S4", 493), ("Peugeot 205 T16", 550), ("Audi Sport Quattro", 450)], 1):
        x = f(i, f"{n} produced {v} bhp at peak", f"https://publisher{i}.test/p", page=f"unique page {i} " * 30)
        fs[x.id] = x
    hint = detect(list(fs.values()))[0].text()
    o = OutlineSlide(n=2, section="S", purpose="Compare power", key_message="Power compared", finding_ids=list(fs), layout_hint="CHART_COMMENTARY", data_hint=hint)
    outline = Outline(arc="timeline", deck_title="D", slides=[o, o.model_copy(update={"n": 3}), o.model_copy(update={"n": 4})])
    draft = SlideDraft(id="s02", section="S", layout="CHART_COMMENTARY", title="Peak power compared across three cars", bullets=["Peugeot led on output"],
                       chart_type="bar", chart_title="Approximate peak power (bhp)", categories=["Lancia", "Peugeot", "Audi"],
                       series=[SeriesDraft(name="bhp", values=[493, 550, 450])], speaker_notes="Figures from the findings.", source_refs=list(fs))
    llm = FakeLLM({"spec_slide": draft, "faithfulness": {"unsupported": [], "vague": []}, "fixer": {"edits": [], "cite": []}})
    s = build_slide(llm, Brief(topic="t", audience="a", slide_count=6), o, outline, fs, Path("brand/default_v1"), [], False)
    assert s.chart and s.chart.series[0].values == [493, 550, 450]
    assert "DATA HINT: native chart candidate" in llm.calls[0]["prompt"] and "493" in llm.calls[0]["prompt"]


# ---- fixes from the first live run -------------------------------------------------------------------------------------------
def test_one_page_listing_several_years_does_not_contradict_itself():
    same_page = "Audi won in 1982 Audi won in 1984 Peugeot won in 1985. " * 6
    findings = [f(1, "In 1982 Audi won the manufacturers title", "https://rally.example.com/titles", "other", page=same_page),
                f(2, "In 1984 Audi won the manufacturers title", "https://rally.example.com/titles", "other", page=same_page)]
    out, conflicts = weigh(llm_for(("f01", "f02")), findings, TierTable())
    assert not conflicts and not any(x.excluded for x in out)         # both true; same origin, so not a conflict


def test_table_hint_keeps_rows_of_one_template_only():
    from deckgen.research.shapes import detect

    def g(i, claim):
        return f(i, claim, f"https://s{i}.example.org/p", page=f"unique page {i} " * 30).model_copy(update={"question": "q"})

    rows = [g(1, "Walter Rohrl won the drivers championship in 1982"), g(2, "Hannu Mikkola won the drivers championship in 1983"),
            g(3, "Stig Blomqvist won the drivers championship in 1984"), g(4, "Timo Salonen won the drivers championship in 1985"),
            g(5, "Lancia won the manufacturers cup of constructors in 1983"), g(6, "Audi finished the manufacturers cup of constructors in 1982")]
    table = [h for h in detect(rows) if h.kind == "table"][0]
    assert [r.label for r in table.rows] == ["1982", "1983", "1984", "1985"] and all(r.finding_id in {"f01", "f02", "f03", "f04"} for r in table.rows)


def test_review_list_groups_warnings_by_check():
    from deckgen.pipeline import review_items
    from deckgen.schemas import DeckSpec, SlideSpec

    spec = DeckSpec(title="t", brand="default_v1", slides=[SlideSpec(id="s01", section="a", layout="TITLE", title="T")])
    notes = {"warnings": [{"outline_slide": n, "check": "layout_variety", "category": "model_compensation", "message": "same layout used 3 times"} for n in (7, 8, 9)]
             + [{"outline_slide": 5, "check": "copy_short", "category": "model_compensation", "message": "repeats 8+ words"}]}
    items = review_items(spec, {}, notes)
    assert len(items) == 2 and "x3" in items[0] and "[7, 8, 9]" in items[0]


# ---- fixes from the niche-topic live run ------------------------------------------------------------------------------------
def test_checker_flagging_unrelated_claims_as_contradictions_is_ignored():
    findings = [f(1, "The vessel was discovered in 1936 near the village of Khujut Rabu", "https://a.example.org/x", "other", page="alpha page " * 40),
                f(2, "Replicas produce between half a volt and two volts with an acid electrolyte", "https://b.example.net/y", "other", page="beta page " * 40)]
    out, conflicts = weigh(llm_for(("f01", "f02")), findings, TierTable())
    assert not conflicts and not any(x.excluded for x in out)       # the checker cried wolf about two different subjects


def test_future_claims_need_support_in_the_findings():
    from deckgen.spec.claims import claim_violations
    from test_claims import FINDINGS, bad

    s = bad(title="Future discoveries will clarify the rules")
    checks = [x.check for x in claim_violations(s, FINDINGS)]
    assert checks.count("overclaim_word") >= 2                       # 'future' and 'will' are not in the cited findings


def test_long_title_slide_fits_its_box(tmp_path):
    import subprocess

    from deckgen.qa import checks
    from deckgen.schemas import DeckSpec, SlideSpec

    spec = DeckSpec(title="t", brand="default_v1", slides=[SlideSpec(id="s01", section="a", layout="TITLE", title="The Baghdad Battery: Evidence and Interpretations",
                                                                       placeholders={"subtitle": "Prepared for a curious general audience"})])
    p = tmp_path / "s.json"
    p.write_text(spec.model_dump_json(exclude_none=True))
    out = tmp_path / "t.pptx"
    r = subprocess.run(["node", "renderer/build_deck.js", str(p), "brand/default_v1", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert [i for i in checks.check(out) if i.type == "overflow"] == []


def test_contested_findings_are_kept_and_marked_for_hedging_by_default():
    findings = [f(1, "The ban took effect for the 1986 season", "https://a.example.org/x", "other", page="alpha text " * 40),
                f(2, "The ban took effect for the 1987 season", "https://b.example.net/y", "other", page="beta words " * 40)]
    out, conflicts = weigh(llm_for(("f01", "f02")), findings, TierTable())
    assert all(x.contested and not x.excluded for x in out)          # both usable, but the writer must say "sources differ"
    assert conflicts[0].status == "unresolved" and len(conflicts[0].sources) == 2
