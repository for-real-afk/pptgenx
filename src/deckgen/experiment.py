"""Experiment harness: run variants (writer model x gate profile) over the SAME cached research findings, several times each, and
compare them on the same metrics. Decide the model and the gate set with data, not with a single lucky run."""
from __future__ import annotations

import json
import shutil
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from deckgen import registry
from deckgen.llm import LLM, Tracer
from deckgen.pipeline import Request, run_pipeline


@dataclass(frozen=True)
class Variant:
    name: str
    writer: str          # "provider:model" for the writer role ("" keeps the configured one)
    gates: str           # "strict" | "demoted"
    note: str = ""


def default_variants(strong: str, cheap: str) -> list[Variant]:
    return [Variant("A", strong, "strict", "strong writer, current (strict) gate set"),
            Variant("B", cheap, "demoted", "cheap writer, gates demoted"),
            Variant("C", strong, "demoted", "strong writer, gates demoted")]


def metrics_from_report(rep: dict, llm: LLM) -> dict:
    tr = getattr(llm, "tracer", None)
    q = rep.get("quality") or {}
    return {
        "slides_built": rep["shortfall"]["built"], "requested": rep["shortfall"]["requested"],
        "blocking": rep.get("qa_blocking_remaining"), "unsourced": len(rep["unsourced_slides"]),
        "specificity": q.get("specificity"), "distinct_evidence": q.get("distinct_evidence"),
        "redundant_pairs": rep["redundancy"]["unresolved"], "fallbacks": rep["fallback_slides"],
        "warnings": rep["shortfall"]["warnings"], "review_items": len(rep["review_before_use"]),
        "first_attempt_pass": (tr.first_attempt_rate("spec_slide_valid") if isinstance(tr, Tracer) else None),
        "llm_calls": tr.calls if isinstance(tr, Tracer) else None,
        "tokens": (tr.tokens_in + tr.tokens_out) if isinstance(tr, Tracer) else None,
        "dropped": rep["shortfall"]["dropped_by_category"],
    }


def run_experiment(base_run: Path, req: Request, variants: list[Variant], runs: int, make_llm: Callable[[Variant, Path], LLM],
                   out_dir: Path, do_qa: bool = False) -> dict:
    """base_run must contain brief.json and findings.json (the cached research). Returns {variant: [metrics...]} and writes summary.md."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, list[dict]] = {}
    for var in variants:
        results[var.name] = []
        for k in range(1, runs + 1):
            rd = out_dir / f"{var.name}-{k}"
            if rd.exists():
                shutil.rmtree(rd)
            rd.mkdir(parents=True)
            for f in ("brief.json", "findings.json"):
                shutil.copy(base_run / f, rd / f)
            llm = make_llm(var, rd)
            t0 = time.time()
            try:
                with registry.use_profile(var.gates):
                    rep = run_pipeline(req, llm, None, rd, auto_approve=True, do_qa=do_qa)  # type: ignore[arg-type]
                m = metrics_from_report(rep, llm)
                m["seconds"] = round(time.time() - t0, 1)
            except Exception as e:  # noqa: BLE001 - a failed run is a result, not a crash
                m = {"error": f"{type(e).__name__}: {str(e)[:160]}", "slides_built": 0}
            results[var.name].append(m)
            (rd / "metrics.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    (out_dir / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    (out_dir / "summary.md").write_text(summary_md(variants, results, req.slides, runs, do_qa), encoding="utf-8")
    return results


def _mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.mean(xs), 2) if xs else None


def summary_md(variants: list[Variant], results: dict, requested: int, runs: int, qa: bool) -> str:
    rows = ["| metric | " + " | ".join(f"{v.name}" for v in variants) + " |", "|---|" + "---|" * len(variants)]
    rows.append("| writer / gates | " + " | ".join(f"{v.writer or 'config'} / {v.gates}" for v in variants) + " |")

    def line(label, fn):
        rows.append(f"| {label} | " + " | ".join(str(fn(results[v.name])) for v in variants) + " |")

    line(f"slides built (of {requested}), mean", lambda r: _mean([m.get("slides_built") for m in r]))
    line("slides built, per run", lambda r: [m.get("slides_built") for m in r])
    line("slide-count spread (max-min)", lambda r: (max(m.get("slides_built", 0) for m in r) - min(m.get("slides_built", 0) for m in r)) if r else None)
    line("failed runs", lambda r: sum("error" in m for m in r))
    for key, label in [("blocking", "blocking issues (QA)" if qa else "blocking issues (QA not run)"), ("unsourced", "unsourced slides"),
                       ("specificity", "specificity"), ("distinct_evidence", "distinct-evidence score"), ("redundant_pairs", "redundant pairs left"),
                       ("fallbacks", "fallback slides"), ("warnings", "warnings"), ("review_items", "review-list length"),
                       ("first_attempt_pass", "first-attempt pass (1 = no fix needed)"), ("llm_calls", "LLM calls"), ("tokens", "tokens")]:
        line(label + ", mean", lambda r, key=key: _mean([m.get(key) for m in r]))
    rows.append("")
    rows.append("Notes: " + "; ".join(f"{v.name} = {v.note}" for v in variants if v.note) + f". {runs} runs per variant over the same cached findings.")
    errs = [f"{n}-{i + 1}: {m['error']}" for n, r in results.items() for i, m in enumerate(r) if "error" in m]
    if errs:
        rows += ["", "Failed runs:"] + [f"- {e}" for e in errs]
    return "\n".join(rows)
