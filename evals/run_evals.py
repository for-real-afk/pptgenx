"""Golden-topic evaluation. Per run: schema first-attempt rates, QA rounds, hard invariants (unsourced = 0, invalid layouts = 0)."""
from __future__ import annotations

import json
import traceback
from pathlib import Path

from deckgen.config import CONFIG
from deckgen.llm import Tracer, make_llm
from deckgen.pipeline import Request, run_pipeline, slug


def run_all(golden: Path, only: str | None = None) -> int:
    from deckgen.cli import make_tools

    topics = json.loads(Path(golden).read_text(encoding="utf-8"))
    rows, failed = [], False
    for t in topics:
        if only and only.lower() not in t["topic"].lower():
            continue
        run_dir = CONFIG.runs_dir / f"eval-{slug(t['topic'])}"
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            rep = run_pipeline(Request(t["topic"], t["audience"], t["slides"]), make_llm(Tracer(run_dir / "trace.jsonl")),
                               make_tools(), run_dir, auto_approve=True)
            hard = bool(rep["unsourced_slides"] or rep["invalid_layouts"] or rep["file_errors"] or rep["render_error"])
            rows.append({"topic": t["topic"][:36], "slides": rep["slides"], "valid@1": rep["first_attempt_rate"].get("spec_slide_valid"),
                         "degraded": rep["first_attempt_rate"].get("spec_slide_degraded"), "unsourced": len(rep["unsourced_slides"]),
                         "bad_layouts": len(rep["invalid_layouts"]), "file_ok": not rep["file_errors"],
                         "quality": rep["quality"], "review_items": len(rep["review_before_use"]), "qa_rounds": len(rep["qa_rounds"]), "blocking_left": rep["qa_blocking_remaining"], "hard_fail": hard})
            failed |= hard
        except Exception as e:  # noqa: BLE001 - an eval run must report, not crash the batch
            (run_dir / "eval_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            rows.append({"topic": t["topic"][:36], "error": f"{type(e).__name__}: {str(e)[:90]}", "hard_fail": True})
            failed = True
    for r in rows:
        print(r)
    out = CONFIG.runs_dir / "eval_summary.json"
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"summary -> {out}")
    return 1 if failed else 0
