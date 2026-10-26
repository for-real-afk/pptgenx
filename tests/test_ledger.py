"""Cost ledger and budget guard, offline: pricing, cap hit mid-run, summary on abort, cache fields, writer prompt split."""
import json

import pytest
from pydantic import BaseModel

from deckgen.ledger import BudgetAbort, Ledger, PriceMissing, Prices
from deckgen.llm import RateLimiter, RoleLLM, RoleSpec, Tracer
from deckgen.providers import register
from deckgen.providers.base import RawResult
from deckgen.providers.fake import Adapter as FakeAdapter

PRICES = Prices({"m": {"input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.1}})  # synthetic USD per million tokens


class Out(BaseModel):
    x: int


class Costly(FakeAdapter):
    """Reports fixed token counts so cost is predictable: 100k in + 1k out = $0.105 at the synthetic prices."""

    def complete(self, **kw):
        r = super().complete(**kw)
        return RawResult(text=r.text, tokens_in=100_000, tokens_out=1_000, model=kw["model"], cache_write_tokens=0, cache_read_tokens=0)


def make(tmp_path, ledger):
    register("costly", Costly())
    roles = {r: RoleSpec(provider="costly", model="m", max_tokens=1000) for r in ("extractor", "writer", "checker", "critic")}
    return RoleLLM(roles, Tracer(tmp_path / "t.jsonl"), cache_dir=tmp_path / "c", use_cache=False,
                   limiter_factory=lambda p: RateLimiter(None, clock=lambda: 0.0, sleep=lambda s: None), ledger=ledger)


def test_missing_price_stops_and_never_guesses():
    with pytest.raises(PriceMissing):
        Prices({"m": {"input": None, "output": 1, "cache_write": 1, "cache_read": 1}}).price("m")
    with pytest.raises(PriceMissing):
        PRICES.price("unknown")


def test_cap_is_never_exceeded_and_summary_written_on_abort(tmp_path):
    ledger = Ledger(tmp_path / "usage.jsonl", PRICES, budget_usd=0.50, echo=lambda s: None)
    llm = make(tmp_path, ledger)
    from deckgen.providers import get_adapter

    get_adapter("costly").script = ['{"x": 1}'] * 50
    big = "x" * 300_000  # counted input ~100k tokens, matching what the fake provider reports
    done = 0
    with pytest.raises(BudgetAbort):
        for _ in range(20):
            llm.generate(stage="outline", system="s", prompt=big + str(done), schema=Out)
            done += 1
    assert done == 4 and ledger.total == pytest.approx(0.42) and ledger.total <= 0.50
    ledger.write_summary(tmp_path / "usage_summary.md")
    text = (tmp_path / "usage_summary.md").read_text(encoding="utf-8")
    assert "ABORTED" in text and "By role" in text and "Ten most expensive calls" in text
    rows = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text().splitlines()]
    assert len(rows) == 4 and rows[-1]["running_total_usd"] == pytest.approx(0.42)
    assert {"input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens", "cost_usd", "cache", "retry", "outcome"} <= set(rows[0])


def test_soft_warnings_and_stage_line(tmp_path):
    seen = []
    ledger = Ledger(None, PRICES, budget_usd=1.0, echo=seen.append)
    ledger.stage = "research"
    ledger.record(role="writer", llm_stage="x", model="m", tin=500_000, tout=20_000)   # $0.60
    ledger.record(role="writer", llm_stage="x", model="m", tin=200_000, tout=0)         # $0.20 -> 80%
    ledger.record(role="writer", llm_stage="x", model="m", tin=50_000, tout=0, cw=0, cr=0)  # 85%
    assert any("60%" in m for m in seen) and any("85%" in m for m in seen)
    assert "research: 3 calls" in ledger.stage_line("research")


def test_cache_tokens_are_priced_separately():
    assert Ledger(None, PRICES, 1.0).cost("m", 0, 0, cw=1_000_000, cr=1_000_000) == pytest.approx(1.35)


def test_writer_prompt_is_split_stable_vs_variable():
    from deckgen.spec.writer_prompt import select_source_text, stable_prefix

    a, b = stable_prefix("style", "menu", "limits"), stable_prefix("style", "menu", "limits")
    assert a == b and "HEDGING" in a and "LAYOUT CHOICE" in a
    txt = select_source_text({"u1": ["alpha beta gamma", "unrelated words"], "u2": ["alpha delta"]}, ["u1", "u2"], "alpha beta")
    assert txt.startswith("[u1]") and "alpha beta gamma" in txt
