"""Experiment harness offline: variants over identical cached findings, per-run metrics, spread, summary table, profile switching."""
import json
from pathlib import Path

from deckgen import registry
from deckgen.experiment import Variant, run_experiment
from deckgen.llm import FakeLLM, Tracer
from deckgen.pipeline import Request
from deckgen.schemas.research import Brief, Finding, Findings
from test_pipeline import FACT, script


def make_base(tmp_path: Path) -> Path:
    base = tmp_path / "base"
    base.mkdir()
    brief = Brief(topic="Group B", audience="fans", slide_count=6, objective="Explain Group B", research_questions=[f"question {i}" for i in range(4)])
    (base / "brief.json").write_text(brief.model_dump_json(), encoding="utf-8")
    fs = [Finding(id=f"f{i:02d}", question=f"question {i % 4}", claim=f"[{i}] {FACT}", evidence_snippet="200 cars 450 480 500 hp", source_type="journalism",
                  source_url=f"https://publisher{i}.test/p", retrieved_at="2026-01-01T00:00:00+00:00", confidence="high", tier=2,
                  origin_id=f"o{i:02d}", independent_origin_count=2, corroborated_by=2) for i in range(1, 9)]
    (base / "findings.json").write_text(Findings(findings=fs, stats={"findings": 8}).model_dump_json(), encoding="utf-8")
    return base


def test_variants_run_over_the_same_findings_and_the_summary_compares_them(tmp_path):
    base = make_base(tmp_path)
    seen_profiles: list[str] = []

    def make(var: Variant, rd: Path):
        llm = FakeLLM(script())
        llm.tracer = Tracer(rd / "t.jsonl")
        orig = llm.generate

        def generate(**kw):
            seen_profiles.append(f"{var.name}:{registry.active_profile()}")
            return orig(**kw)

        llm.generate = generate
        return llm

    variants = [Variant("A", "groq:strong", "strict", "strong, strict"), Variant("B", "groq:cheap", "demoted", "cheap, demoted")]
    out = tmp_path / "exp"
    results = run_experiment(base, Request("Group B", "fans", 6), variants, 2, make, out, do_qa=False)
    assert set(results) == {"A", "B"} and all(len(v) == 2 for v in results.values())
    assert all("error" not in m for v in results.values() for m in v), results
    assert {m["slides_built"] for v in results.values() for m in v} == {6}                 # same findings, same scripts: no spread
    # every LLM call of a variant ran under that variant's gate profile, restored afterwards
    assert set(seen_profiles) == {"A:strict", "B:demoted"} and registry.active_profile() == registry.CONFIG.gate_profile
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "slide-count spread" in md and "groq:strong / strict" in md and "groq:cheap / demoted" in md and "2 runs per variant" in md
    run_report = json.loads((out / "B-1" / "run_report.json").read_text(encoding="utf-8"))
    assert run_report["gate_profile"] == "demoted" and run_report["shortfall"]["requested"] == 6


def test_a_failing_run_is_reported_not_raised(tmp_path):
    base = make_base(tmp_path)

    def make(var, rd):
        class Boom:
            tracer = Tracer(None)

            def generate(self, **kw):
                raise RuntimeError("provider down")

        return Boom()

    out = tmp_path / "exp2"
    results = run_experiment(base, Request("Group B", "fans", 6), [Variant("A", "", "strict")], 1, make, out)
    assert "provider down" in results["A"][0]["error"]
    assert "Failed runs" in (out / "summary.md").read_text(encoding="utf-8")
