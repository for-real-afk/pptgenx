"""LLM layer. Pipeline stages depend on `LLM` (stage name in, validated Pydantic object out) and never on a provider SDK.

`RoleLLM` maps each stage to a ROLE (extractor / writer / checker / critic), each role to provider + model via config, and adds
what every provider needs: an on-disk cache keyed by prompt hash, rate-limit awareness (provider headers + local TPM cap),
per-role budgets, tracing, and parse-validate-retry with the validation error fed back. Correctness never depends on
provider-side schema enforcement: output is always validated with Pydantic here."""
from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from deckgen.config import CONFIG
from deckgen.providers import get_adapter
from deckgen.providers.base import ProviderUnavailable, RateLimited

T = TypeVar("T", bound=BaseModel)
NL = chr(10)

ROLES = ("extractor", "writer", "checker", "critic")
STAGE_ROLE = {
    "plan": "extractor", "outline": "writer", "spec_slide": "writer", "spec_fallback_labels": "writer", "fixer": "writer",
    "research_query": "extractor", "research_extract": "extractor", "image_queries": "extractor",
    "research_crosscheck": "checker", "research_conflict_pairs": "checker", "faithfulness": "checker", "deck_consistency": "checker",
    "critic": "critic", "critic_consistency": "critic",
}


class BudgetExceeded(RuntimeError):
    pass


class QuotaExhausted(RuntimeError):
    pass


class LLM(Protocol):
    def generate(self, *, stage: str, system: str, prompt: str, schema: type[T],
                 images: list[Path] | None = None, vision: bool = False, cache_prefix: str = "") -> T: ...


@dataclass
class RoleSpec:
    provider: str
    model: str
    fallbacks: tuple = ()  # ((provider, model), ...) tried when the primary's daily quota is gone
    temperature: float = 0.1
    seed: int | None = 7
    max_tokens: int = 4096


def parse_role(value: str, **kw) -> RoleSpec:
    """'groq:openai/gpt-oss-120b' or 'groq:a,gemini:b' (first is primary, the rest fallbacks)."""
    pairs = [tuple(p.strip().split(":", 1)) for p in value.split(",") if p.strip()]
    return RoleSpec(provider=pairs[0][0], model=pairs[0][1], fallbacks=tuple(pairs[1:]), **kw)


class Tracer:
    """Append-only trace of every LLM call (role, model, prompt hash, tokens, latency, cache hit, rate-limit wait)."""

    def __init__(self, path: Path | None):
        self.path, self.calls, self.tokens_in, self.tokens_out = path, 0, 0, 0
        self.models: set[str] = set()
        self.attempts: dict[str, list[int]] = {}  # stage -> attempt number of each successful call
        self.roles: dict[str, dict] = {}

    def log(self, **rec) -> None:
        self.calls += 1
        self.tokens_in += rec.get("tokens_in") or 0
        self.tokens_out += rec.get("tokens_out") or 0
        if rec.get("model"):
            self.models.add(rec["model"])
        if role := rec.get("role"):
            r = self.roles.setdefault(role, {"calls": 0, "cache_hits": 0, "tokens_in": 0, "tokens_out": 0, "waited_s": 0.0, "models": set()})
            r["calls"] += 1
            r["cache_hits"] += bool(rec.get("cached"))
            r["tokens_in"] += rec.get("tokens_in") or 0
            r["tokens_out"] += rec.get("tokens_out") or 0
            r["waited_s"] += rec.get("waited_s") or 0.0
            if rec.get("model"):
                r["models"].add(rec["model"])
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

    def first_attempt_rate(self, stage: str) -> float | None:
        a = self.attempts.get(stage)
        return None if not a else sum(1 for x in a if x == 1) / len(a)

    def role_summary(self) -> dict:
        return {k: {**v, "models": sorted(v["models"]), "waited_s": round(v["waited_s"], 1)} for k, v in self.roles.items()}

    def summary(self) -> str:
        hits = sum(r["cache_hits"] for r in self.roles.values())
        return f"{self.calls} LLM calls ({hits} from cache), {self.tokens_in} tokens in, {self.tokens_out} tokens out"


def parse_json_as(schema: type[T], text: str) -> T:
    """Strip code fences, parse, validate with Pydantic."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    return schema.model_validate_json(t)


class RateLimiter:
    """Per-provider: local sliding-window TPM cap plus whatever the provider's headers last said. Waits instead of failing."""

    def __init__(self, tpm: int | None = None, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.tpm, self.clock, self.sleep = tpm, clock, sleep
        self.window: deque = deque()
        self.remaining: int | None = None
        self.reset_at = 0.0
        self.waited = 0.0

    def _wait(self, s: float) -> None:
        s = max(0.0, min(s, 120.0))
        if s > 0:
            self.waited += s
            self.sleep(s)

    def before(self, est_tokens: int) -> None:
        for _ in range(50):
            now = self.clock()
            while self.window and now - self.window[0][0] >= 60:
                self.window.popleft()
            used = sum(t for _, t in self.window)
            if self.tpm and used + est_tokens > self.tpm and self.window:
                self._wait(self.window[0][0] + 60 - now + 0.2)
                continue
            if self.remaining is not None and est_tokens > self.remaining and self.reset_at > now:
                self._wait(self.reset_at - now + 0.5)
                self.remaining = None
                continue
            return

    def after(self, tokens: int, remaining: int | None, reset_s: float | None) -> None:
        now = self.clock()
        self.window.append((now, tokens))
        if remaining is not None:
            self.remaining = remaining
            self.reset_at = now + (reset_s or 0.0)

    def on_429(self, retry_after: float) -> None:
        self._wait(retry_after + 0.5)


def est_tokens(system: str, prompt: str, images: list | None, max_out: int) -> int:
    return int((len(system) + len(prompt)) / 3.5) + 1100 * len(images or []) + min(max_out, 1500)


class RoleLLM:
    def __init__(self, roles: dict, tracer: Tracer | None = None, *, cache_dir: Path | None = None,
                 use_cache: bool | None = None, tpm: dict | None = None, limiter_factory=None,
                 budgets: dict | None = None, max_calls: int | None = None, ledger=None):
        self.ledger = ledger
        self.roles, self.tracer = roles, tracer or Tracer(None)
        self.cache_dir = cache_dir if cache_dir is not None else CONFIG.runs_dir / "_cache" / "llm"
        self.use_cache = CONFIG.llm_cache if use_cache is None else use_cache
        self.budgets = budgets if budgets is not None else CONFIG.role_budgets
        self.max_calls = max_calls if max_calls is not None else CONFIG.max_llm_calls
        self.used: dict = {r: {"calls": 0, "tokens": 0} for r in ROLES}
        self._tpm = tpm if tpm is not None else CONFIG.tpm
        self._limiters: dict = {}
        self._limiter_factory = limiter_factory

    def limiter(self, provider: str) -> RateLimiter:
        if provider not in self._limiters:
            self._limiters[provider] = (self._limiter_factory(provider) if self._limiter_factory
                                        else RateLimiter(self._tpm.get(provider)))
        return self._limiters[provider]

    # -- budget -----------------------------------------------------------------------------------------------------
    def _check_budget(self, role: str) -> None:
        b, u = self.budgets.get(role, {}), self.used[role]
        if b.get("calls") and u["calls"] >= b["calls"]:
            raise BudgetExceeded(f"role '{role}' call budget {b['calls']} reached")
        if b.get("tokens") and u["tokens"] >= b["tokens"]:
            raise BudgetExceeded(f"role '{role}' token budget {b['tokens']} reached")
        if self.max_calls and sum(x["calls"] for x in self.used.values()) >= self.max_calls:
            raise BudgetExceeded(f"overall LLM call cap {self.max_calls} reached")

    def budget_report(self) -> dict:
        return {r: {**self.used[r], "limit": self.budgets.get(r, {})} for r in ROLES if self.used[r]["calls"]}

    # -- cache ------------------------------------------------------------------------------------------------------
    def _key(self, role, spec, provider, model, system, prompt, schema, images) -> str:
        img = [hashlib.sha256(Path(i).read_bytes()).hexdigest()[:16] for i in images or []]
        blob = json.dumps([provider, model, role, spec.temperature, spec.seed, system, prompt, schema.__name__,
                           json.dumps(schema.model_json_schema(), sort_keys=True), img], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _cache_get(self, key: str):
        p = self.cache_dir / f"{key}.json"
        if self.use_cache and p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
        return None

    def _cache_put(self, key: str, rec: dict) -> None:
        if self.use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{key}.json").write_text(json.dumps(rec), encoding="utf-8")

    # -- main -------------------------------------------------------------------------------------------------------
    def _saved_usd(self, model: str, hit: dict) -> float:
        if not self.ledger or not hit.get("tin") and not hit.get("tout"):
            return 0.0
        try:
            return self.ledger.cost(model, hit.get("tin", 0), hit.get("tout", 0), hit.get("cw", 0), hit.get("cr", 0))
        except Exception:  # noqa: BLE001 - savings are informational; a missing price must not break a cache hit
            return 0.0

    def generate(self, *, stage, system, prompt, schema, images=None, vision=False, cache_prefix=""):
        role = STAGE_ROLE.get(stage) or ("critic" if (images or vision) else "writer")
        spec = self.roles[role]
        full = f"{prompt}{NL}{NL}Respond with JSON only, matching this JSON Schema:{NL}{json.dumps(schema.model_json_schema())}"
        candidates = [(spec.provider, spec.model)] + ([] if CONFIG.pin_models else list(spec.fallbacks))
        last = None
        for provider, model in candidates:
            key = self._key(role, spec, provider, model, system, cache_prefix + NL + full, schema, images)
            hit = self._cache_get(key)
            if hit is not None:
                try:
                    out = parse_json_as(schema, hit["text"])
                    self.tracer.log(stage=stage, role=role, provider=provider, model=model, cached=True, tokens_in=0, tokens_out=0,
                                    prompt_hash=key[:12], attempt=hit.get("attempt", 1))
                    self.tracer.attempts.setdefault(stage, []).append(hit.get("attempt", 1))
                    if self.ledger:
                        self.ledger.record(role=role, llm_stage=stage, model=model, tin=0, tout=0, cache="hit", saved_usd=self._saved_usd(model, hit))
                    return out
                except (ValidationError, ValueError):
                    pass  # stale or bad cache entry: recompute
            self._check_budget(role)
            try:
                return self._call(stage, role, spec, provider, model, system, cache_prefix, full, schema, images, key)
            except QuotaExhausted as e:
                last = e
        raise QuotaExhausted(f"{stage}: daily quota exhausted for {candidates}: {last}")

    def _call(self, stage, role, spec, provider, model, system, cache_prefix, full, schema, images, key):
        adapter, lim = get_adapter(provider), self.limiter(provider)
        schema_json = json.dumps(schema.model_json_schema())
        text_prompt, last_err, t0 = full, None, time.time()
        for attempt in range(1, CONFIG.parse_attempts + 1):
            waited0, raw = lim.waited, None
            if self.ledger:  # hard budget guard: stop BEFORE a call that could exceed the cap (raises BudgetAbort)
                self.ledger.check(model, system, cache_prefix + text_prompt, spec.max_tokens, len(images or []))
            for net in range(1, CONFIG.net_attempts + 1):
                lim.before(est_tokens(system, cache_prefix + text_prompt, images, spec.max_tokens))
                try:
                    raw = adapter.complete(model=model, system=system, prompt=text_prompt, images=images, temperature=spec.temperature,
                                           seed=spec.seed, max_tokens=spec.max_tokens, schema_json=schema_json, cache_prefix=cache_prefix)
                    break
                except RateLimited as e:
                    if e.daily:
                        raise QuotaExhausted(str(e)) from e
                    lim.on_429(e.retry_after)
                except ProviderUnavailable:
                    lim._wait(min(30, 2 ** net))
            if raw is None:
                raise RuntimeError(f"{stage}: provider '{provider}' unavailable after {CONFIG.net_attempts} tries")
            lim.after(raw.tokens_in + raw.tokens_out, raw.remaining_tokens, raw.reset_seconds)
            self.used[role]["calls"] += 1
            self.used[role]["tokens"] += raw.tokens_in + raw.tokens_out + raw.cache_write_tokens + raw.cache_read_tokens
            self.tracer.log(stage=stage, role=role, provider=provider, model=raw.model or model, cached=False, prompt_hash=key[:12],
                            tokens_in=raw.tokens_in, tokens_out=raw.tokens_out, latency_s=round(time.time() - t0, 2),
                            attempt=attempt, waited_s=round(lim.waited - waited0, 2))
            try:
                out = parse_json_as(schema, raw.text or "")
                outcome = "ok"
            except (ValidationError, ValueError) as e:
                last_err, out, outcome = e, None, "schema_fail"
            if raw.stop_reason == "max_tokens":  # a cut-off answer is a failed call even if it happens to parse; it is billed and logged as such
                last_err, out, outcome = ValueError(f"response was cut off at max_tokens={spec.max_tokens}; answer more briefly"), None, "truncated"
            if self.ledger:  # the API's own token counts, priced from prices.yaml
                self.ledger.record(role=role, llm_stage=stage, model=model, tin=raw.tokens_in, tout=raw.tokens_out, cw=raw.cache_write_tokens,
                                   cr=raw.cache_read_tokens, latency_s=time.time() - t0, cache="miss", retry=attempt, outcome=outcome)
            if outcome == "ok":
                self.tracer.attempts.setdefault(stage, []).append(attempt)
                self._cache_put(key, {"text": raw.text, "attempt": attempt, "tin": raw.tokens_in, "tout": raw.tokens_out,
                                      "cw": raw.cache_write_tokens, "cr": raw.cache_read_tokens})
                return out
            text_prompt = f"{full}{NL}{NL}Your previous output was invalid: {str(last_err)[:500]}{NL}Return corrected JSON only."
        raise ValueError(f"{stage}: unparseable after {CONFIG.parse_attempts} attempts: {str(last_err)[:300]}")


class GeminiLLM(RoleLLM):
    """Back-compat: every role on Gemini (used by the live tests)."""

    def __init__(self, tracer: Tracer | None = None, max_calls: int | None = None):
        super().__init__(default_roles("gemini"), tracer, max_calls=max_calls)


def default_roles(provider: str | None = None) -> dict:
    return CONFIG.build_roles(provider)


def make_llm(tracer: Tracer | None = None, ledger=None) -> RoleLLM:
    return RoleLLM(default_roles(), tracer, ledger=ledger)


class FakeLLM:
    """Scripted responses keyed by stage; each value is a model instance or a callable(prompt, images)."""

    def __init__(self, scripts: dict):
        self.scripts, self.calls = scripts, []

    def generate(self, *, stage, system, prompt, schema, images=None, vision=False, cache_prefix=""):
        prompt = cache_prefix + prompt
        self.calls.append({"stage": stage, "prompt": prompt, "images": images, "system": system})
        s = self.scripts[stage]
        out = s(prompt, images) if callable(s) else s
        return schema.model_validate(out.model_dump() if isinstance(out, BaseModel) else out)
