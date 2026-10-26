"""Single-agent loop, offline: a scripted model (tool calls and answers), fake search/build/render, real file tools, real ledger and budget guard."""
import json

import pytest
from PIL import Image

from deckgen.agent.loop import trim
from deckgen.agent.run import run_agent
from deckgen.agent.tools import AgentTools
from deckgen.config import CONFIG
from deckgen.ledger import Ledger, Prices
from deckgen.research.tools import SearchResult
from deckgen.schemas.qa import Issue

PRICES = Prices({"m-main": {"input": 2.0, "output": 10.0, "cache_write": 2.5, "cache_read": 0.2}, "m-read": {"input": 0.5, "output": 2.5, "cache_write": 0.6, "cache_read": 0.05}})

SPEC = {"title": "T", "brand": "default_v1", "slides": [
    {"id": "s01", "section": "A", "layout": "TITLE", "title": "A deck about a topic", "placeholders": {"subtitle": "For the audience"}},
    {"id": "s02", "section": "A", "layout": "TITLE_ONLY", "title": "A specific claim about the topic here",
     "composition": {"kind": "cards", "items": [{"icon": "FaBook", "heading": "One", "body": "Short body text one."}, {"icon": "FaBook", "heading": "Two", "body": "Short body text two."}]},
     "speaker_notes": "Notes sentence one. Sentence two."},
    {"id": "s03", "section": "End", "layout": "SOURCES", "title": "Sources", "placeholders": {"body": ["A page (site.test)"]}}]}


class FakeSearch:
    def search(self, query, n):
        return [SearchResult(title="A page", url="https://site.test/a", snippet="snippet text", content="")]


def fake_build(spec, brand_dir, out):
    out.write_bytes(b"pptx " + spec.model_dump_json().encode())


def fake_render(deck, out):
    (out / "images").mkdir(parents=True, exist_ok=True)
    for i in range(1, 4):
        Image.new("RGB", (1600, 900), "white").save(out / "images" / f"slide-{i}.png")


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


def run(tmp_path, responses, budget=8.0):
    tr = scripted(responses)
    out = run_agent("Topic", "audience", 14, tmp_path / "run", budget=budget, yes=True, transport=tr, search=FakeSearch(), builder=fake_build,
                    renderer=fake_render, inspector=lambda deck, spec: [], echo=lambda s: None)
    return out, tr


def happy_path():
    return [
        reply(use(1, "search", query="topic")),
        reply(use(2, "write_file", path="outline.md", content="1. Question one")),
        reply({"type": "text", "text": "Outline written."}, stop="end_turn"),               # the loop answers with the auto-approval
        reply(use(3, "write_file", path="spec.json", content=json.dumps(SPEC))),
        reply(use(4, "validate_spec", path="spec.json")),
        reply(use(5, "build_deck", spec_path="spec.json")),
        reply(use(6, "render_slides", deck_path="deck_1.pptx", slides=[1, 2, 3])),
        reply(use(7, "write_file", path="review.md", content="# Review before use" + chr(10) + chr(10) + "- one claim rests on one source" + chr(10))),
        reply(use(8, "finish", deck_path="deck_1.pptx", review_notes_path="review.md")),
    ]


def test_scripted_run_end_to_end(tmp_path, roles):
    r, tr = run(tmp_path, happy_path())
    run_dir = tmp_path / "run"
    assert r["stop_reason"] == "finished" and (run_dir / "deck.pptx").exists() and (run_dir / "review_before_use.md").exists()
    assert r["slides_built"] == 3 and r["slides_viewed"] == 3 and r["never_viewed"] == []
    assert (run_dir / "approval.log").read_text().startswith("outline auto-approved")
    assert (run_dir / "usage_summary.md").exists() and (run_dir / "run_report.md").exists()
    rows = [json.loads(line) for line in (run_dir / "usage.jsonl").read_text().splitlines()]
    assert {x["role"] for x in rows} == {"agent", "tool"} and sum(x["role"] == "agent" for x in rows) == 9
    assert r["total_usd"] > 0 and r["review_items"] == 1 and "DECKGEN_CONTACT is not set" in r["photos"]
    first = tr.calls[0]
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"} and first["tools"][-1]["cache_control"] == {"type": "ephemeral"}   # stable prefix is cached
    assert "Layouts (names, placeholders" in first["system"][0]["text"]


def test_budget_abort_never_exceeds_the_cap_and_keeps_the_summary(tmp_path, roles):
    big = [reply(use(i, "search", query=f"q{i}"), tin=60_000, tout=500) for i in range(1, 40)]
    r, tr = run(tmp_path, big, budget=0.30)
    assert r["aborted"] and r["total_usd"] <= 0.30 and r["tool_calls"] < 39
    assert "ABORTED" in (tmp_path / "run" / "usage_summary.md").read_text() and "budget guard" in r["stop_reason"]


def make_tools(tmp_path, inspector=lambda d, s: []):
    return AgentTools(tmp_path, CONFIG.brands_dir / "default_v1", Ledger(None, PRICES, 8.0, echo=lambda s: None), haiku_model="m-read", max_slides=13,
                      search=FakeSearch(), builder=fake_build, renderer=fake_render, inspector=inspector)


def test_finish_is_refused_until_the_spec_validates_and_blocking_issues_are_gone(tmp_path, roles):
    blocking: list = []
    t = make_tools(tmp_path, lambda deck, spec: blocking)
    t.call("write_file", {"path": "spec.json", "content": json.dumps(SPEC)})
    t.call("write_file", {"path": "review.md", "content": "- x"})
    out, err = t.call("build_deck", {"spec_path": "spec.json"})
    assert not err and "NOTE: this spec was not validated" in out
    out, err = t.call("finish", {"deck_path": "deck_1.pptx", "review_notes_path": "review.md"})
    assert err and "did not pass validate_spec" in out
    assert t.call("validate_spec", {"path": "spec.json"})[1] is False
    blocking.append(Issue(slide=2, type="overflow", severity="high", description="text overflows", suggested_fix="shorten", source="auto"))
    t.call("build_deck", {"spec_path": "spec.json"})
    out, err = t.call("finish", {"deck_path": "deck_2.pptx", "review_notes_path": "review.md"})
    assert err and "blocking automated issues" in out and not t.finished
    blocking.clear()
    t.call("build_deck", {"spec_path": "spec.json"})
    assert t.call("finish", {"deck_path": "deck_3.pptx", "review_notes_path": "review.md"})[1] is False and (tmp_path / "deck.pptx").exists()


def test_files_stay_inside_work_and_validation_errors_are_precise(tmp_path, roles):
    t = make_tools(tmp_path)
    out, err = t.call("write_file", {"path": "../escape.md", "content": "x"})
    assert err and "outside work" in out and not (tmp_path / "escape.md").exists()
    bad = json.loads(json.dumps(SPEC))
    bad["slides"][1]["title"] = "x" * 200
    bad["slides"][1]["composition"]["items"][0]["icon"] = "FaNotAnIcon"
    t.call("write_file", {"path": "spec.json", "content": json.dumps(bad)})
    out, err = t.call("validate_spec", {"path": "spec.json"})
    assert err and "title" in out and "FaNotAnIcon" in out
    assert t.call("nope", {})[1] is True


def test_render_rounds_are_capped_and_old_images_are_trimmed(tmp_path, roles):
    t = make_tools(tmp_path)
    t.call("write_file", {"path": "spec.json", "content": json.dumps(SPEC)})
    t.call("build_deck", {"spec_path": "spec.json"})
    for _ in range(4):
        blocks, err = t.call("render_slides", {"deck_path": "deck_1.pptx", "slides": [1]})
        assert not err and any(b["type"] == "image" for b in blocks)
    blocks, _ = t.call("render_slides", {"deck_path": "deck_1.pptx", "slides": [1]})
    assert not any(b["type"] == "image" for b in blocks) and "stop changing it" in blocks[0]["text"]
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}}
    msgs = [{"role": "user", "content": "task"},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": [img]}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "b", "content": [img]}]}]
    out = trim(msgs, {"a": "render_slides", "b": "render_slides"})
    assert out[1]["content"][0]["content"][0]["type"] == "text" and out[2]["content"][0]["content"][0]["type"] == "image"
