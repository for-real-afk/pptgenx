"""Token and cost ledger with a hard budget guard.

Every LLM call is recorded in usage.jsonl (provider-reported token counts, never estimates) and priced from config/prices.yaml.
Before each call the guard estimates the WORST-CASE cost (counted input at the highest input-side price, plus max_tokens at the
output price) and aborts if running total + estimate would exceed the cap. Prices are never guessed: a model without prices raises."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from deckgen.config import CONFIG

CHARS_PER_TOKEN_GUARD = 3.0  # conservative (English is about 4): the guard over-estimates input rather than under-estimates
SOFT_WARNINGS = (0.60, 0.85)


class PriceMissing(RuntimeError):
    pass


class BudgetAbort(RuntimeError):
    pass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_write: float
    cache_read: float


class Prices:
    def __init__(self, table: dict[str, dict]):
        self.table = table

    @classmethod
    def load(cls, path: Path | None = None) -> Prices:
        path = path or CONFIG.root / "config" / "prices.yaml"
        if not path.exists():
            raise PriceMissing(f"{path} does not exist: create it with per-million-token prices for every model in use")
        return cls((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("models", {}))

    def price(self, model: str) -> Price:
        row = self.table.get(model)
        if not row:
            raise PriceMissing(f"no prices for model '{model}' in prices.yaml")
        missing = [k for k in ("input", "output", "cache_write", "cache_read") if row.get(k) is None]
        if missing:
            raise PriceMissing(f"prices.yaml has no {missing} for model '{model}'")
        return Price(float(row["input"]), float(row["output"]), float(row["cache_write"]), float(row["cache_read"]))

    def missing(self, models: list[str]) -> list[str]:
        out = []
        for m in models:
            try:
                self.price(m)
            except PriceMissing as e:
                out.append(str(e))
        return out


class Ledger:
    def __init__(self, path: Path | None, prices: Prices, budget_usd: float, echo: Callable[[str], None] = print):
        self.path, self.prices, self.budget, self.echo = path, prices, float(budget_usd), echo
        self.rows: list[dict] = []
        self.total = 0.0
        self.stage = "setup"
        self._warned: set[float] = set()
        self.aborted = ""
        if path and path.exists():  # a resumed run id keeps counting against the same cap
            self.rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.total = sum(r["cost_usd"] for r in self.rows)

    # -- pricing ---------------------------------------------------------------------------------------------------
    def cost(self, model: str, tin: int, tout: int, cw: int = 0, cr: int = 0) -> float:
        p = self.prices.price(model)
        return (tin * p.input + tout * p.output + cw * p.cache_write + cr * p.cache_read) / 1_000_000

    def worst_case(self, model: str, est_input_tokens: int, max_out: int) -> float:
        p = self.prices.price(model)
        return (est_input_tokens * max(p.input, p.cache_write) + max_out * p.output) / 1_000_000

    def check(self, model: str, system: str, prompt: str, max_out: int, n_images: int = 0) -> None:
        """Abort BEFORE a call that could push the total over the cap."""
        est_in = int((len(system) + len(prompt)) / CHARS_PER_TOKEN_GUARD) + 1500 * n_images
        est = self.worst_case(model, est_in, max_out)
        if self.total + est > self.budget:
            self.aborted = (f"budget guard: running total ${self.total:.4f} + worst-case ${est:.4f} for the next {model} call "
                            f"would exceed the ${self.budget:.2f} cap; stopped before spending")
            raise BudgetAbort(self.aborted)

    # -- recording -------------------------------------------------------------------------------------------------
    def record(self, *, role: str, llm_stage: str, model: str, tin: int, tout: int, cw: int = 0, cr: int = 0, latency_s: float = 0.0,
               cache: str = "miss", retry: int = 1, outcome: str = "ok", saved_usd: float = 0.0, error: str = "", context_tokens: int = 0) -> float:
        usd = 0.0 if cache == "hit" else self.cost(model, tin, tout, cw, cr)
        self.total += usd
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "pipeline_stage": self.stage, "role": role, "stage": llm_stage, "model": model,
               "input_tokens": tin, "output_tokens": tout, "cache_write_tokens": cw, "cache_read_tokens": cr, "cost_usd": round(usd, 6),
               "latency_s": round(latency_s, 2), "cache": cache, "retry": retry, "outcome": outcome, "saved_usd": round(saved_usd, 6), "error": error, "context_tokens": context_tokens,
               "running_total_usd": round(self.total, 6)}
        self.rows.append(row)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        for frac in SOFT_WARNINGS:
            if self.total >= frac * self.budget and frac not in self._warned:
                self._warned.add(frac)
                self.echo(f"[budget] WARNING: {int(frac * 100)}% of the ${self.budget:.2f} cap used (${self.total:.2f})")
        return usd

    def log_tool(self, name: str, latency_s: float, ok: bool, error: str = "") -> None:
        """A tool call (search, fetch, build, ...) is logged beside the model calls; it costs nothing here (model calls it makes are recorded themselves)."""
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "pipeline_stage": self.stage, "role": "tool", "stage": name, "model": "-", "input_tokens": 0,
               "output_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.0, "latency_s": round(latency_s, 2), "cache": "miss",
               "retry": 1, "outcome": "ok" if ok else "failed", "saved_usd": 0.0, "error": error[:200], "running_total_usd": round(self.total, 6)}
        self.rows.append(row)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + chr(10))

    def spent(self, stage: str) -> float:
        """Money spent so far on model calls made while `pipeline_stage` was `stage`."""
        return sum(r["cost_usd"] for r in self.rows if r["pipeline_stage"] == stage and r["role"] != "tool")

    @staticmethod
    def account_spent(runs_dir: Path) -> float:
        """Total cost recorded in every run's usage.jsonl. Repair and fix-pass runs copy their source run's rows, so identical rows count once."""
        seen: set[tuple] = set()
        total = 0.0
        for f in runs_dir.glob("*/usage.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                key = (r.get("ts"), r.get("stage"), r.get("model"), r.get("input_tokens"), r.get("output_tokens"), r.get("cost_usd"), r.get("latency_s"))
                if key not in seen and r.get("cache") != "hit":
                    seen.add(key)
                    total += r.get("cost_usd", 0.0)
        return total

    # -- reporting -------------------------------------------------------------------------------------------------
    def stage_line(self, name: str) -> str:
        rows = [r for r in self.rows if r["pipeline_stage"] == name]
        calls = sum(r["cache"] != "hit" for r in rows)
        tin = sum(r["input_tokens"] + r["cache_write_tokens"] + r["cache_read_tokens"] for r in rows)
        tout = sum(r["output_tokens"] for r in rows)
        usd = sum(r["cost_usd"] for r in rows)
        line = f"[usage] {name}: {calls} calls, {tin} tokens in, {tout} out, ${usd:.4f}; running total ${self.total:.4f} of ${self.budget:.2f}"
        self.echo(line)
        return line

    def summary_md(self) -> str:
        def agg(key):
            d = defaultdict(lambda: [0, 0, 0, 0.0])
            for r in self.rows:
                if r["cache"] == "hit" or r["role"] == "tool":
                    continue
                x = d[r[key]]
                x[0] += 1
                x[1] += r["input_tokens"] + r["cache_write_tokens"] + r["cache_read_tokens"]
                x[2] += r["output_tokens"]
                x[3] += r["cost_usd"]
            return d

        def table(title, key):
            rows = [f"### {title}", "| name | calls | tokens in | tokens out | cost USD |", "|---|---|---|---|---|"]
            for k, (c, i, o, u) in sorted(agg(key).items(), key=lambda kv: -kv[1][3]):
                rows.append(f"| {k} | {c} | {i} | {o} | {u:.4f} |")
            return rows

        tools = [r for r in self.rows if r["role"] == "tool"]
        live = [r for r in self.rows if r["cache"] != "hit" and r["role"] != "tool"]
        hits = [r for r in self.rows if r["cache"] == "hit"]
        cw = sum(r["cache_write_tokens"] for r in live)
        cr = sum(r["cache_read_tokens"] for r in live)
        waste = [r for r in live if r["outcome"] != "ok" or r["retry"] > 1]
        out = ["# Usage summary", "",
               f"Total **${self.total:.4f}** of the ${self.budget:.2f} cap ({100 * self.total / self.budget:.1f}%). Calls: {len(live)} paid, {len(hits)} served from the local prompt cache."]
        if tools:
            out += [f"Tool calls: {len(tools)} ({sum(t['outcome'] != 'ok' for t in tools)} failed)."]
        if self.aborted:
            out += ["", f"**ABORTED**: {self.aborted}"]
        out += [""] + table("By role", "role") + [""] + table("By model", "model") + [""] + table("By pipeline stage", "pipeline_stage")
        out += ["", "### Ten most expensive calls", "| stage | role | model | in | out | cost USD | outcome |", "|---|---|---|---|---|---|---|"]
        for r in sorted(live, key=lambda r: -r["cost_usd"])[:10]:
            out.append(f"| {r['stage']} | {r['role']} | {r['model']} | {r['input_tokens'] + r['cache_write_tokens'] + r['cache_read_tokens']} | {r['output_tokens']} | {r['cost_usd']:.4f} | {r['outcome']} (retry {r['retry']}) |")
        out += ["", "### Caching",
                f"- provider prompt cache: {cw} tokens written, {cr} tokens read ({100 * cr / max(cr + cw + sum(r['input_tokens'] for r in live), 1):.1f}% of input tokens served from cache)",
                f"- local prompt-hash cache: {len(hits)} hits, about ${sum(r['saved_usd'] for r in hits):.4f} not spent",
                "", "### Retry waste",
                f"- {len(waste)} calls were retries or failed schema/checks, costing ${sum(r['cost_usd'] for r in waste):.4f} ({100 * sum(r['cost_usd'] for r in waste) / max(self.total, 1e-9):.1f}% of spend)"]
        return "\n".join(out)

    def write_summary(self, path: Path) -> None:
        path.write_text(self.summary_md(), encoding="utf-8")
