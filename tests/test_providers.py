"""Provider layer, offline: role routing, cache, validation-feedback retry, rate limiting, budgets, adapters' request/response handling."""
import json

import httpx
import pytest
from pydantic import BaseModel

from deckgen import config as config_mod
from deckgen import llm as llm_mod
from deckgen.llm import BudgetExceeded, QuotaExhausted, RateLimiter, RoleLLM, RoleSpec, Tracer, parse_role
from deckgen.providers import get_adapter, register
from deckgen.providers.base import ProviderUnavailable, RateLimited, parse_duration
from deckgen.providers.fake import Adapter as FakeAdapter


class Out(BaseModel):
    x: int


@pytest.fixture
def two_fakes():
    a, b = FakeAdapter(), FakeAdapter()
    register("fakea", a)
    register("fakeb", b)
    return a, b


def roles(writer=("fakea", "m-writer"), checker=("fakeb", "m-checker")):
    spec = lambda p, **k: RoleSpec(provider=p[0], model=p[1], **k)
    return {"extractor": spec(writer), "writer": spec(writer), "checker": spec(checker, temperature=0.0), "critic": spec(checker)}


def make(tmp_path, r=None, sleeps=None, use_cache=True, budgets=None, max_calls=0):
    sl = sleeps if sleeps is not None else []
    return RoleLLM(r or roles(), Tracer(tmp_path / "t.jsonl"), cache_dir=tmp_path / "cache", use_cache=use_cache,
                   limiter_factory=lambda p: RateLimiter(None, clock=lambda: 0.0, sleep=sl.append),
                   budgets=budgets or {}, max_calls=max_calls)


def test_stages_route_to_their_role_and_models_are_traced(tmp_path, two_fakes):
    a, b = two_fakes
    a.script, b.script = ['{"x": 1}'], ['{"x": 2}']
    llm = make(tmp_path)
    assert llm.generate(stage="spec_slide", system="s", prompt="p", schema=Out).x == 1      # writer -> fakea
    assert llm.generate(stage="faithfulness", system="s", prompt="p", schema=Out).x == 2    # checker -> fakeb
    assert a.calls[0]["model"] == "m-writer" and b.calls[0]["model"] == "m-checker"
    summ = llm.tracer.role_summary()
    assert summ["writer"]["models"] == ["m-writer"] and summ["checker"]["models"] == ["m-checker"]
    lines = [json.loads(line) for line in (tmp_path / "t.jsonl").read_text().splitlines()]
    assert {(r["role"], r["model"]) for r in lines} == {("writer", "m-writer"), ("checker", "m-checker")}


def test_cache_hit_means_zero_provider_calls_and_no_cache_disables_it(tmp_path, two_fakes):
    a, _ = two_fakes
    a.script = ['{"x": 5}']
    assert make(tmp_path).generate(stage="outline", system="s", prompt="p", schema=Out).x == 5
    again = make(tmp_path)  # a fresh run: new object, same cache dir
    assert again.generate(stage="outline", system="s", prompt="p", schema=Out).x == 5
    assert len(a.calls) == 1 and again.tracer.role_summary()["writer"]["cache_hits"] == 1
    a.script = ['{"x": 6}']
    assert make(tmp_path, use_cache=False).generate(stage="outline", system="s", prompt="p", schema=Out).x == 6 and len(a.calls) == 2


def test_invalid_output_is_retried_with_the_validation_error(tmp_path, two_fakes):
    a, _ = two_fakes
    a.script = ['{"x": "not an int"}', '{"x": 3}']
    llm = make(tmp_path)
    assert llm.generate(stage="outline", system="s", prompt="p", schema=Out).x == 3
    assert "previous output was invalid" in a.calls[1]["prompt"] and llm.tracer.attempts["outline"] == [2]


def test_429_waits_and_retries_instead_of_failing(tmp_path, two_fakes):
    a, _ = two_fakes
    a.script = [RateLimited(7.0), '{"x": 1}']
    sleeps: list = []
    assert make(tmp_path, sleeps=sleeps).generate(stage="outline", system="s", prompt="p", schema=Out).x == 1
    assert sleeps and sleeps[0] >= 7.0


def test_provider_headers_and_local_tpm_cap_cause_waits():
    sleeps, now = [], [0.0]

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    lim = RateLimiter(tpm=1000, clock=lambda: now[0], sleep=sleep)
    lim.before(600)
    lim.after(600, None, None)
    lim.before(600)  # would exceed 1000 per minute: must wait for the window to slide
    assert sleeps and sleeps[0] >= 59
    sleeps.clear()
    lim2 = RateLimiter(clock=lambda: now[0], sleep=sleep)
    lim2.after(100, remaining=50, reset_s=10.0)  # the provider said: 50 tokens left, reset in 10 s
    lim2.before(400)
    assert sleeps and sleeps[0] >= 10


def test_daily_quota_falls_back_unless_models_are_pinned(tmp_path, two_fakes, monkeypatch):
    a, b = two_fakes
    r = roles()
    r["writer"] = RoleSpec(provider="fakea", model="m1", fallbacks=(("fakeb", "m2"),))
    a.script, b.script = [RateLimited(1, daily=True)], ['{"x": 9}']
    assert make(tmp_path, r).generate(stage="outline", system="s", prompt="p", schema=Out).x == 9
    a.script = [RateLimited(1, daily=True)]
    monkeypatch.setattr(llm_mod, "CONFIG", config_mod.Config(pin_models=True))
    with pytest.raises(QuotaExhausted):
        make(tmp_path / "pinned", r).generate(stage="outline", system="s", prompt="p-pinned", schema=Out)


def test_budget_guard_stops_a_role(tmp_path, two_fakes):
    a, _ = two_fakes
    a.script = ['{"x": 1}', '{"x": 2}']
    llm = make(tmp_path, budgets={"writer": {"calls": 1}})
    llm.generate(stage="outline", system="s", prompt="p1", schema=Out)
    with pytest.raises(BudgetExceeded):
        llm.generate(stage="outline", system="s", prompt="p2", schema=Out)
    assert llm.budget_report()["writer"]["calls"] == 1


def test_switching_a_role_to_another_provider_is_config_only(tmp_path, two_fakes, monkeypatch):
    a, b = two_fakes
    b.script = ['{"x": 4}']
    monkeypatch.setenv("DECKGEN_ROLE_WRITER", "fakeb:some-model")
    spec = config_mod.Config().build_roles()["writer"]
    assert (spec.provider, spec.model) == ("fakeb", "some-model")
    llm = make(tmp_path, {**roles(), "writer": spec})
    assert llm.generate(stage="outline", system="s", prompt="p", schema=Out).x == 4 and not a.calls


def test_unknown_provider_names_the_missing_file():
    with pytest.raises(ValueError, match="providers/nope.py"):
        get_adapter("nope")


def test_parse_helpers():
    assert parse_duration("2m59.56s") == pytest.approx(179.56) and parse_duration("450ms") == pytest.approx(0.45)
    r = parse_role("groq:openai/gpt-oss-120b,gemini:g")
    assert (r.provider, r.model, r.fallbacks) == ("groq", "openai/gpt-oss-120b", (("gemini", "g"),))


# ---- adapters: request shape and response handling with a stubbed httpx ------------------------------------------------------
class Resp:
    def __init__(self, status=200, body=None, headers=None, text=""):
        self.status_code, self._b, self.headers, self.text = status, body or {}, headers or {}, text or json.dumps(body or {})

    def json(self):
        return self._b

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("x", request=None, response=None)


def test_groq_adapter_request_and_rate_limit_headers(monkeypatch):
    from deckgen.providers import groq

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    seen = {}

    def post(url, headers, json, timeout):
        seen.update(url=url, body=json, auth=headers["Authorization"])
        return Resp(200, {"choices": [{"message": {"content": '{"x":1}'}}], "usage": {"prompt_tokens": 10, "completion_tokens": 3}, "model": json["model"]},
                    {"x-ratelimit-remaining-tokens": "4000", "x-ratelimit-reset-tokens": "2m59.56s"})

    monkeypatch.setattr(groq.httpx, "post", post)
    r = groq.Adapter().complete(model="openai/gpt-oss-120b", system="s", prompt="p", images=None, temperature=0.1, seed=7, max_tokens=500, schema_json="{}")
    assert seen["url"].endswith("/chat/completions") and seen["auth"] == "Bearer test-key"
    b = seen["body"]
    assert b["response_format"] == {"type": "json_object"} and b["seed"] == 7 and b["reasoning_effort"] == "low" and b["include_reasoning"] is False
    assert r.text == '{"x":1}' and r.remaining_tokens == 4000 and r.reset_seconds == pytest.approx(179.56) and r.tokens_out == 3


def test_groq_adapter_429_5xx_and_vision_guard(monkeypatch):
    from deckgen.providers import groq

    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr(groq.httpx, "post", lambda *a, **k: Resp(429, headers={"retry-after": "12"}, text="Rate limit reached for tokens per day (TPD)"))
    with pytest.raises(RateLimited) as e:
        groq.Adapter().complete(model="m", system="s", prompt="p", images=None, temperature=0, seed=None, max_tokens=10, schema_json=None)
    assert e.value.retry_after == 12 and e.value.daily
    monkeypatch.setattr(groq.httpx, "post", lambda *a, **k: Resp(503, text="down"))
    with pytest.raises(ProviderUnavailable):
        groq.Adapter().complete(model="m", system="s", prompt="p", images=None, temperature=0, seed=None, max_tokens=10, schema_json=None)
    with pytest.raises(ValueError, match="no vision"):
        groq.Adapter().complete(model="llama-3.3-70b-versatile", system="s", prompt="p", images=["x.jpg"], temperature=0, seed=None, max_tokens=10, schema_json=None)


def test_anthropic_adapter_request_and_response(monkeypatch):
    from deckgen.providers import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seen = {}

    def post(url, headers, json, timeout):
        seen.update(url=url, headers=headers, body=json)
        return Resp(200, {"content": [{"type": "text", "text": '{"x": 2}'}], "usage": {"input_tokens": 5, "output_tokens": 2}, "model": json["model"]},
                    {"anthropic-ratelimit-tokens-remaining": "900"})

    monkeypatch.setattr(anthropic.httpx, "post", post)
    r = anthropic.Adapter().complete(model="claude-x", system="sys", prompt="p", images=None, temperature=0, seed=7, max_tokens=100, schema_json=None)
    assert seen["url"].endswith("/v1/messages") and seen["headers"]["x-api-key"] == "test-key" and seen["body"]["system"][0]["text"] == "sys" and seen["body"]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "seed" not in seen["body"] and r.text == '{"x": 2}' and r.remaining_tokens == 900 and r.tokens_in == 5
