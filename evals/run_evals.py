"""Golden-topic evaluation, round 2: several runs per topic over SHARED research, a go/no-go verdict, and a comparison with the baseline.

Run 1 of a topic does the research (then outline, spec, QA); runs 2..N copy its brief and findings and redo outline, spec and QA with the LLM
cache off, so slide-count spread measures the writer, not the search engine. Pin one model per role for comparable numbers."""
from __future__ import annotations

import json
import shutil
import traceback
from pathlib import Path

from deckgen.config import CONFIG
from deckgen.experiment import metrics_from_report
from deckgen.llm import RoleLLM, Tracer, default_roles
from deckgen.pipeline import Request, run_pipeline, slug

BUILT_MIN = 0.8        # share of requested slides that must be built, on EVERY run of every topic
SPREAD_MAX = 1         # max - min slides built across a topic's runs
FALLBACK_MAX = 1       # per deck
REVIEW_MAX = 15        # items in review_before_use.md: "short and readable"


def gonogo(by_topic: dict[str, list[dict]]) -> dict:
    """Automated part of the go/no-go. The manual read of two decks is always reported as pending: only a human can settle it."""
    verdict: dict = {"topics": {}, "manual": "PENDING: a human must read two generated decks and their review lists for known-wrong facts"}
    for topic, runs in by_topic.items():
        ok_runs = [m for m in runs if "error" not in m]
        built = [m["slides_built"] for m in ok_runs]
        req = ok_runs[0]["requested"] if ok_runs else (runs[0].get("requested") if runs else None)
        checks = {
            "all runs completed": len(ok_runs) == len(runs) and bool(runs),
            f">= {int(BUILT_MIN * 100)}% of requested slides on every run": bool(ok_runs) and min(built) >= BUILT_MIN * req,
            "zero blocking issues": bool(ok_runs) and all(m.get("blocking") == 0 for m in ok_runs),
            "zero unsourced claims": bool(ok_runs) and all(m.get("unsourced") == 0 for m in ok_runs),
            f"slide-count spread <= {SPREAD_MAX}": bool(ok_runs) and (max(built) - min(built)) <= SPREAD_MAX,
            f"<= {FALLBACK_MAX} fallback slide per deck": bool(ok_runs) and all(m.get("fallbacks", 0) <= FALLBACK_MAX for m in ok_runs),
            f"review list <= {REVIEW_MAX} items": bool(ok_runs) and all(m.get("review_items", 99) <= REVIEW_MAX for m in ok_runs),
        }
        verdict["topics"][topic] = {"checks": checks, "pass": all(checks.values()), "built": built, "requested": req}
    verdict["automated_pass"] = bool(verdict["topics"]) and all(t["pass"] for t in verdict["topics"].values())
    return verdict


def comparison_md(by_topic: dict[str, list[dict]], verdict: dict, baseline: list[dict]) -> str:
    base = {b["topic"]: b for b in baseline}
    rows = ["| topic | requested | baseline built (round 1) | now built (per run) | spread | blocking | unsourced | fallbacks | review items | automated go/no-go |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for topic, runs in by_topic.items():
        b = base.get(topic, {})
        ok = [m for m in runs if "error" not in m]
        built = [m.get("slides_built", 0) for m in runs]
        v = verdict["topics"][topic]
        rows.append(f"| {topic[:46]} | {v['requested']} | {b.get('built', 'n/a')} | {built} | {(max(built) - min(built)) if built else 'n/a'} | "
                    f"{[m.get('blocking') for m in ok]} | {[m.get('unsourced') for m in ok]} | {[m.get('fallbacks') for m in ok]} | "
                    f"{[m.get('review_items') for m in ok]} | {'GO' if v['pass'] else 'NO-GO'} |")
    out = ["# Eval, round 2", "", *rows, "", f"Automated verdict: **{'GO' if verdict['automated_pass'] else 'NO-GO'}**. Manual read: {verdict['manual']}.", ""]
    for topic, v in verdict["topics"].items():
        failed = [k for k, ok in v["checks"].items() if not ok]
        if failed:
            out.append(f"- {topic[:60]}: fails {failed}")
    errs = [f"{t[:50]}: {m['error']}" for t, rs in by_topic.items() for m in rs if "error" in m]
    return "\n".join(out + (["", "Run errors:"] + [f"- {e}" for e in errs] if errs else []))


def run_topic(t: dict, runs: int, out_root: Path, make_tools, make_llm, do_qa: bool = True) -> list[dict]:
    base = out_root / slug(t["topic"])
    results: list[dict] = []
    for k in range(1, runs + 1):
        rd = base / f"run{k}"
        rd.mkdir(parents=True, exist_ok=True)
        if k > 1:
            for f in ("brief.json", "findings.json"):
                shutil.copy(base / "run1" / f, rd / f)
        req = Request(t["topic"], t["audience"], t["slides"], tier_overrides=t.get("tier_overrides"))
        llm = make_llm(Tracer(rd / "trace.jsonl"), use_cache=(k == 1))
        try:
            rep = run_pipeline(req, llm, make_tools() if k == 1 else None, rd, auto_approve=True, do_qa=do_qa)  # type: ignore[arg-type]
            results.append(metrics_from_report(rep, llm))
        except Exception as e:  # noqa: BLE001 - an eval run must report, not crash the batch
            (rd / "eval_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            results.append({"error": f"{type(e).__name__}: {str(e)[:160]}", "slides_built": 0, "requested": t["slides"]})
            if k == 1:
                break  # no research, nothing to repeat
    return results


def run_all(golden: Path, only: str | None = None, runs: int = 3) -> int:
    from deckgen.cli import make_tools

    topics = json.loads(Path(golden).read_text(encoding="utf-8"))
    out_root = CONFIG.runs_dir / "eval2"
    make_llm = lambda tracer, use_cache=True: RoleLLM(default_roles(), tracer, use_cache=use_cache)  # noqa: E731
    by_topic: dict[str, list[dict]] = {}
    for t in topics:
        if only and only.lower() not in t["topic"].lower():
            continue
        by_topic[t["topic"]] = run_topic(t, runs, out_root, make_tools, make_llm)
    verdict = gonogo(by_topic)
    baseline = json.loads((Path(__file__).parent / "baseline.json").read_text(encoding="utf-8"))["rows"]
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "summary.md").write_text(comparison_md(by_topic, verdict, baseline), encoding="utf-8")
    (out_root / "summary.json").write_text(json.dumps({"results": by_topic, "verdict": verdict}, indent=1), encoding="utf-8")
    print((out_root / "summary.md").read_text(encoding="utf-8"))
    return 0 if verdict["automated_pass"] else 1
