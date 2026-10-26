"""Single-agent loop v3, offline: a scripted model, fake search/pages/render, the real Node runner for builds, the real ledger, phase budget and tools."""
import json

import pytest
from PIL import Image
from pptx import Presentation

from deckgen.agent.phases import PhaseBudget
from deckgen.agent.run import run_agent
from deckgen.agent.tools import AgentTools
from deckgen.config import CONFIG
from deckgen.ledger import Ledger, Prices
from deckgen.research.tools import SearchResult

PRICES = Prices({"m-main": {"input": 2.0, "output": 10.0, "cache_write": 2.5, "cache_read": 0.2}, "m-read": {"input": 0.5, "output": 2.5, "cache_write": 0.6, "cache_read": 0.05}})
BRAND = CONFIG.brands_dir / "default_v1"

GOOD_SCRIPT = """
const SLIDES = [{ title: "A specific claim about the topic here", body: ["First point", "Second point"], notes: "Source: site.test." }];
module.exports = async ({ pres, C, theme, h, CONTENT }) => {
  const s0 = pres.addSlide({ masterName: "TITLE" });
  s0.addText("Deck title", { placeholder: "title" });
  s0.addText("Subtitle", { placeholder: "subtitle" });
  for (const d of SLIDES) {
    const s = pres.addSlide({ masterName: "TITLE_ONLY" });
    s.addText(d.title, { placeholder: "title" });
    const c = h.ctx(pres, s, C, theme);
    h.bullets(c, d.body, { x: CONTENT.x, y: CONTENT.y, w: 7, h: 3 });
    h.notes(c, d.notes);
  }
};
"""
OVERFLOW_SCRIPT = GOOD_SCRIPT.replace("h.bullets(c, d.body, { x: CONTENT.x, y: CONTENT.y, w: 7, h: 3 });",
                                      'h.textBox(c, "word ".repeat(400), { x: CONTENT.x, y: CONTENT.y, w: 3, h: 1, fontSize: 18 });')
READ_OUTSIDE_SCRIPT = 'module.exports = async () => { require("fs").readFileSync("../../.env", "utf8"); };'


class FakeSearch:
    def search(self, query, n):
        return [SearchResult(title="A page", url="https://site.test/a", snippet="snippet text", content="")]


def fake_renderer(deck, out):
    (out / "images").mkdir(parents=True, exist_ok=True)
    for i in range(1, len(Presentation(deck).slides) + 1):
        Image.new("RGB", (1600, 900), "white").save(out / "images" / f"slide-{i:02d}.jpg")


def page(url):
    return {"title": f"Page {url[-3:]}", "text": ("Some readable fact about the topic, stated plainly. " * 20), "links": []}


def make_tools(tmp_path, **kw):
    ledger = Ledger(None, PRICES, 12.0, echo=lambda s: None)
    phases = PhaseBudget(12.0, 0.25, 0.35, 0.30, 0.10)
    opts = {"haiku_model": "m-read", "max_slides": 14, "max_pages": 12, "free_pages": 5, "max_slide_rounds": 3, "search": FakeSearch(), "renderer": fake_renderer, "page_loader": page}
    tools = AgentTools(tmp_path, BRAND, ledger, phases, **{**opts, **kw})
    return tools, ledger, phases


def use(i, name, **args):
    return {"type": "tool_use", "id": f"t{i}", "name": name, "input": args}


def reply(*blocks, tin=1000, tout=200, stop="tool_use"):
    return {"content": list(blocks), "stop_reason": stop, "usage": {"input_tokens": tin, "output_tokens": tout, "cache_creation_input_tokens": 500, "cache_read_input_tokens": 2000}}


def scripted(responses):
    it = iter(responses)
    calls = []

    def transport(body):
        calls.append(body)
        return next(it)

    transport.calls = calls
    return transport


@pytest.fixture
def roles(monkeypatch):
    for r, m in (("EXTRACTOR", "m-read"), ("WRITER", "m-main"), ("CHECKER", "m-main"), ("CRITIC", "m-read")):
        monkeypatch.setenv(f"DECKGEN_ROLE_{r}", f"anthropic:{m}")
    monkeypatch.setattr(Prices, "load", classmethod(lambda cls, path=None: PRICES))


def run(tmp_path, responses, budget=12.0):
    tr = scripted(responses)
    out = run_agent("Topic", "audience", 14, tmp_path / "run", budget=budget, yes=True, transport=tr, search=FakeSearch(), renderer=fake_renderer, page_loader=page, echo=lambda s: None)
    return out, tr


def happy_path():
    return [
        reply(use(1, "search", query="topic")),
        reply(use(2, "fetch_page", url="https://site.test/a")),
        reply(use(3, "write_file", path="outline.md", content="1. Question one")),
        reply({"type": "text", "text": "Outline written."}, stop="end_turn"),              # no tool call: the loop answers with the auto-approval
        reply(use(4, "write_build_script", path="build.js", content=GOOD_SCRIPT)),
        reply(use(5, "run_build")),
        reply(use(6, "render_slides", deck_path="deck_1.pptx", slides=[1, 2])),
        reply(use(7, "write_file", path="review.md", content="# Review before use\n\n- one claim rests on one source\n")),
        reply(use(8, "finish", deck_path="deck_1.pptx", review_notes_path="review.md")),
    ]


def test_scripted_run_end_to_end(tmp_path, roles):
    r, tr = run(tmp_path, happy_path())
    run_dir = tmp_path / "run"
    assert r["stop_reason"] == "finished" and (run_dir / "deck.pptx").exists() and (run_dir / "review_before_use.md").exists() and (run_dir / "build_script.js").exists()
    assert r["slides_built"] == 2 and r["never_viewed"] == [] and r["pages"] == 1 and r["builds"] == 1 and r["review_items"] == 1
    assert (run_dir / "approval.log").read_text().startswith("outline auto-approved")
    assert (run_dir / "usage_summary.md").exists() and (run_dir / "run_report.md").exists() and "DECKGEN_CONTACT is not set" in r["photos"]
    rows = [json.loads(line) for line in (run_dir / "usage.jsonl").read_text().splitlines()]
    agent_rows = [x for x in rows if x["role"] == "agent"]
    assert len(agent_rows) == 9 and all(x["context_tokens"] > 0 for x in agent_rows) and r["context"]["turns"] == 9      # context size is logged per turn
    assert {x["pipeline_stage"] for x in agent_rows} <= {"research", "write", "look", "wrapup"}
    assert tr.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"} and "Layout names and placeholders" in tr.calls[0]["system"][0]["text"]


def test_a_page_beyond_the_free_ones_needs_a_named_gap(tmp_path, roles):
    tools, _, _ = make_tools(tmp_path, free_pages=2, max_pages=4)
    for n in (1, 2):
        assert not tools.call("fetch_page", {"url": f"https://site.test/p{n}"})[1]
    out, err = tools.call("fetch_page", {"url": "https://site.test/p3"})
    assert err and "named gap" in out
    out, err = tools.call("fetch_page", {"url": "https://site.test/p3", "gap": "the exact date of the final event"})
    assert err and "no GAP line" in out                                                   # claimed a gap that notes.md does not contain
    tools.call("write_file", {"path": "notes.md", "content": "GAP: the exact date of the final event, needed for the timeline slide\n"})
    assert not tools.call("fetch_page", {"url": "https://site.test/p3", "gap": "the exact date of the final event"})[1]
    assert not tools.call("fetch_page", {"url": "https://site.test/p4", "gap": "the exact date of the final event"})[1]
    out, err = tools.call("fetch_page", {"url": "https://site.test/p5", "gap": "the exact date of the final event"})
    assert err and "page cap reached" in out                                               # the hard cap (4) stops it before the per-gap limit matters


def test_sixth_fetch_without_a_gap_is_refused_with_default_limits(tmp_path, roles):
    tools, _, _ = make_tools(tmp_path)
    for n in range(1, 6):
        assert not tools.call("fetch_page", {"url": f"https://site.test/p{n}"})[1]
    assert tools.call("fetch_page", {"url": "https://site.test/p6"})[1] is True


def test_research_stops_at_its_cap_and_hands_over(tmp_path, roles):
    big = reply(use(1, "search", query="q"), tin=1_600_000, tout=3000)                         # one expensive research turn: above 25% of $12
    responses = [big, reply(use(2, "search", query="again")), reply(use(3, "write_build_script", path="build.js", content=GOOD_SCRIPT)),
                 reply(use(4, "run_build")), reply(use(5, "render_slides", deck_path="deck_1.pptx", slides=[1, 2])),
                 reply(use(6, "write_file", path="review.md", content="- a\n")), reply(use(7, "finish", deck_path="deck_1.pptx", review_notes_path="review.md"))]
    r, tr = run(tmp_path, responses)
    second_request = json.dumps(tr.calls[1]["messages"])
    assert "HANDOVER" in second_request and "research budget is used up" in second_request
    assert "research is closed" in json.dumps(tr.calls[2]["messages"])                       # the search after the handover was refused
    assert r["stop_reason"] == "finished" and any("research" in n for n in r["phase_log"]) and r["pages"] == 0


def test_no_render_round_starts_without_budget_and_the_agent_is_told_to_wrap_up(tmp_path, roles):
    def heavy(*blocks):
        return reply(*blocks, tin=330_000, tout=1500)                                        # about $1 a turn: three turns cost more than the money above the reserve

    responses = [reply(use(1, "write_build_script", path="build.js", content=GOOD_SCRIPT)), reply(use(2, "run_build")),
                 heavy(use(3, "render_slides", deck_path="deck_1.pptx", slides=[1, 2])), heavy(use(4, "render_slides", deck_path="deck_1.pptx", slides=[1])),
                 reply(use(5, "run_build")), reply(use(6, "write_file", path="review.md", content="- unfixed: none\n")),
                 reply(use(7, "finish", deck_path="deck_1.pptx", review_notes_path="review.md"))]
    r, tr = run(tmp_path, responses, budget=3.0)
    assert any("WRAP-UP" in json.dumps(c["messages"]) for c in tr.calls)
    assert "budget for further build rounds is used up" in json.dumps(tr.calls[-1]["messages"])      # the build after the wrap-up message was refused
    assert r["stop_reason"] == "finished" and r["total_usd"] <= 3.0


def test_budget_abort_never_exceeds_the_cap_and_keeps_the_summary(tmp_path, roles):
    big = [reply(use(i, "read_file", path="notes.md"), tin=60_000, tout=500) for i in range(1, 40)]
    r, _ = run(tmp_path, big, budget=0.30)
    assert r["aborted"] and r["total_usd"] <= 0.30 and r["tool_calls"] < 39
    assert "ABORTED" in (tmp_path / "run" / "usage_summary.md").read_text() and "budget guard" in r["stop_reason"]


def test_build_script_builds_a_valid_deck_and_reports_an_overflow_with_its_slide(tmp_path, roles):
    tools, _, _ = make_tools(tmp_path)
    tools.call("write_build_script", {"path": "build.js", "content": GOOD_SCRIPT})
    out, err = tools.call("run_build", {})
    assert not err and "File validation: OK" in out and "Overflow or off-slide issues: 0" in out
    tools.call("write_build_script", {"path": "bad.js", "content": OVERFLOW_SCRIPT})
    out, err = tools.call("run_build", {"script_path": "bad.js"})
    assert not err and "slide 2" in out and "overflow" in out and "Overflow or off-slide issues: 1" in out


def test_a_script_cannot_read_outside_work_or_use_the_network(tmp_path, roles):
    tools, _, _ = make_tools(tmp_path)
    tools.call("write_build_script", {"path": "evil.js", "content": READ_OUTSIDE_SCRIPT})
    out, err = tools.call("run_build", {"script_path": "evil.js"})
    assert err and "outside the allowed folders" in out and "line 1" in out
    tools.call("write_build_script", {"path": "net.js", "content": 'module.exports = async () => {\n require("https");\n};'})
    out, err = tools.call("run_build", {"script_path": "net.js"})
    assert err and "not available" in out and "line 2" in out
    tools.call("write_build_script", {"path": "syntax.js", "content": "module.exports = async () => { oops( };"})
    assert tools.call("run_build", {"script_path": "syntax.js"})[1] is True


def test_finish_refuses_until_clean_viewed_and_documented(tmp_path, roles):
    tools, _, _ = make_tools(tmp_path)
    tools.call("write_build_script", {"path": "bad.js", "content": OVERFLOW_SCRIPT})
    tools.call("run_build", {"script_path": "bad.js"})
    tools.call("write_file", {"path": "review.md", "content": "- x"})
    out, err = tools.call("finish", {"deck_path": "deck_1.pptx", "review_notes_path": "review.md"})
    assert err and "overflow or off-slide issues on slides [2]" in out and "not looked at slides [1, 2]" in out
    tools.call("write_build_script", {"path": "build.js", "content": GOOD_SCRIPT})
    tools.call("run_build", {"script_path": "build.js"})
    out, err = tools.call("finish", {"deck_path": "deck_2.pptx", "review_notes_path": "review.md"})
    assert err and "not looked at" in out and "overflow" not in out
    tools.call("render_slides", {"deck_path": "deck_2.pptx", "slides": [1, 2]})
    assert tools.call("finish", {"deck_path": "work/deck_2.pptx", "review_notes_path": "review.md"})[1] is False and tools.finished
    assert tools.builder.best().deck.name == "deck_2.pptx"                                  # the build with fewer hard issues is the best one


def test_slide_views_are_capped_and_files_stay_in_work(tmp_path, roles):
    tools, _, _ = make_tools(tmp_path, max_slide_rounds=1)
    tools.call("write_build_script", {"path": "build.js", "content": GOOD_SCRIPT})
    tools.call("run_build", {})
    for _ in range(2):
        blocks, err = tools.call("render_slides", {"deck_path": "deck_1.pptx", "slides": [1]})
        assert not err and any(b["type"] == "image" for b in blocks)
    blocks, _ = tools.call("render_slides", {"deck_path": "deck_1.pptx", "slides": [1]})
    assert not any(b["type"] == "image" for b in blocks) and "stop changing it" in blocks[0]["text"]
    out, err = tools.call("write_file", {"path": "../escape.md", "content": "x"})
    assert err and "outside work" in out and not (tmp_path / "escape.md").exists()
    assert "Subtitle" in tools.call("slide_text", {"deck_path": "deck_1.pptx"})[0]


def test_phase_fractions_must_fit_the_budget():
    with pytest.raises(ValueError):
        PhaseBudget(12.0, 0.5, 0.4, 0.3, 0.1)
    assert PhaseBudget(12.0, 0.25, 0.35, 0.30, 0.10).usable == pytest.approx(10.8)


def test_old_images_and_bulky_results_are_trimmed():
    from deckgen.agent.loop import trim

    img = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}}
    msgs = [{"role": "user", "content": "task"},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": [img]}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "b", "content": [img]}]}]
    out = trim(msgs, {"a": "render_slides", "b": "render_slides"})
    assert out[1]["content"][0]["content"][0]["type"] == "text" and out[2]["content"][0]["content"][0]["type"] == "image"
    thinking = [{"role": "user", "content": "t"}, {"role": "assistant", "content": [{"type": "thinking", "thinking": "x", "signature": "s"}, {"type": "text", "text": "hi"}]}]
    assert [b["type"] for b in trim(thinking, {})[1]["content"]] == ["text"]


def test_the_best_build_is_the_real_deck_not_a_trivial_probe(tmp_path, roles):
    from deckgen.agent.builder import BuildRecord

    tools, _, _ = make_tools(tmp_path)
    b = tools.builder
    b.builds = {"probe": BuildRecord(deck=tmp_path / "probe.pptx", slides=1, issues=[]),
                "real": BuildRecord(deck=tmp_path / "real.pptx", slides=13, issues=[]),
                "broken": BuildRecord(deck=tmp_path / "broken.pptx", slides=14, issues=[__import__("deckgen.schemas.qa", fromlist=["Issue"]).Issue(
                    slide=3, type="overflow", severity="high", description="x", suggested_fix="y", source="auto")])}
    assert b.best().deck.name == "real.pptx"                      # fewest hard issues first, then the most slides


def test_edit_build_script_replaces_one_exact_piece(tmp_path, roles):
    tools, _, _ = make_tools(tmp_path)
    tools.call("write_build_script", {"path": "build.js", "content": GOOD_SCRIPT})
    out, err = tools.call("edit_build_script", {"path": "build.js", "old": "First point", "new": "Changed point"})
    assert not err and "Changed point" in tools.work.read("build.js")
    assert tools.call("edit_build_script", {"path": "build.js", "old": "h.ctx(pres", "new": "x"})[1] is False      # unique
    out, err = tools.call("edit_build_script", {"path": "build.js", "old": "placeholder", "new": "x"})
    assert err and "must match exactly once" in out


def test_the_agent_is_told_to_wrap_up_before_the_tool_call_limit(tmp_path, roles):
    responses = [reply(use(i, "read_file", path="notes.md")) for i in range(1, 12)] + [reply(use(12, "write_file", path="review.md", content="- x\n"))]
    tr = scripted(responses + [reply({"type": "text", "text": "done"}, stop="end_turn")] * 10)
    from deckgen.agent.loop import AgentLoop
    from deckgen.agent.tools import tool_definitions

    tools, ledger, phases = make_tools(tmp_path)
    loop = AgentLoop(tools, ledger, phases, model="m-main", system_text="s", tool_defs=tool_definitions(), max_calls=10, max_seconds=60, transport=tr)
    loop.run("task")
    assert any("tool call limit is nearly reached" in json.dumps(c["messages"]) for c in tr.calls) and phases.phase == "wrapup"
