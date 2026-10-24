"""LLM interface. Stages depend on `LLM`, never on a provider SDK, so the provider is swappable."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from deckgen.config import CONFIG

T = TypeVar("T", bound=BaseModel)


class BudgetExceeded(RuntimeError):
    pass


class LLM(Protocol):
    def generate(self, *, stage: str, system: str, prompt: str, schema: type[T],
                 images: list[Path] | None = None, vision: bool = False) -> T: ...


class Tracer:
    """Append-only trace of every LLM call: stage, prompt hash, tokens, latency."""

    def __init__(self, path: Path | None):
        self.path, self.calls, self.tokens_in, self.tokens_out = path, 0, 0, 0
        self.models: set[str] = set()
        self.attempts: dict[str, list[int]] = {}  # stage -> attempt number of each successful call

    def log(self, **rec) -> None:
        self.calls += 1
        self.tokens_in += rec.get("tokens_in") or 0
        self.tokens_out += rec.get("tokens_out") or 0
        if rec.get("model"):
            self.models.add(rec["model"])
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

    def first_attempt_rate(self, stage: str) -> float | None:
        a = self.attempts.get(stage)
        return None if not a else sum(1 for x in a if x == 1) / len(a)

    def summary(self) -> str:
        return f"{self.calls} LLM calls, {self.tokens_in} tokens in, {self.tokens_out} tokens out"


def parse_json_as(schema: type[T], text: str) -> T:
    """Strip code fences, parse, validate with Pydantic (never trust provider enforcement alone)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    return schema.model_validate_json(t)


class GeminiLLM:
    def __init__(self, tracer: Tracer | None = None, max_calls: int | None = None):
        from google import genai

        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.model = CONFIG.model or "gemini-3.5-flash"
        self.vision_model = CONFIG.vision_model or self.model
        self.tracer = tracer or Tracer(None)
        self.max_calls = max_calls if max_calls is not None else CONFIG.max_llm_calls

    def _call(self, model, parts, cfg):
        """Backoff on 429/503, then fall back to the stable model. Returns (response, model used)."""
        from google.genai import errors

        for m in ((model,) if CONFIG.pin_models else (model, *CONFIG.fallback_models)):
            for k in range(5 if CONFIG.pin_models else 3):
                try:
                    return self.client.models.generate_content(model=m, contents=parts, config=cfg), m
                except (errors.ServerError, errors.ClientError) as e:
                    code = getattr(e, "code", 0)
                    if code == 429 and "PerDay" in str(e):
                        break  # daily quota for this model is gone: sleeping is pointless, try the next model
                    if code not in (429, 500, 503):
                        raise
                    time.sleep(min(30, 2 ** k * 2))
        raise RuntimeError("Gemini unavailable after retries and fallback")

    def generate(self, *, stage, system, prompt, schema, images=None, vision=False):
        from google.genai import types

        if self.tracer.calls >= self.max_calls:
            raise BudgetExceeded(f"LLM call cap {self.max_calls} reached")
        full = f"{prompt}\n\nRespond with JSON only, matching this JSON Schema:\n{json.dumps(schema.model_json_schema())}"
        parts: list = [full]
        for img in images or []:
            parts.append(types.Part.from_bytes(data=Path(img).read_bytes(), mime_type="image/jpeg"))
        cfg = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json", temperature=0.2)
        model = self.vision_model if (vision or images) else self.model
        t0, last_err = time.time(), None
        for attempt in (1, 2):  # one repair attempt on unparseable output
            r, model = self._call(model, parts, cfg)
            um = r.usage_metadata
            self.tracer.log(stage=stage, model=model, prompt_hash=hashlib.sha256(full.encode()).hexdigest()[:12],
                            tokens_in=getattr(um, "prompt_token_count", 0), tokens_out=getattr(um, "candidates_token_count", 0),
                            latency_s=round(time.time() - t0, 2), attempt=attempt)
            try:
                out = parse_json_as(schema, r.text or "")
                self.tracer.attempts.setdefault(stage, []).append(attempt)
                return out
            except (ValidationError, ValueError) as e:
                last_err = e
                parts = [full, f"Your previous output was invalid: {str(e)[:500]}. Return corrected JSON only."]
        raise ValueError(f"{stage}: unparseable after retry: {str(last_err)[:300]}")


def make_llm(tracer: Tracer | None = None) -> LLM:
    if CONFIG.llm_provider == "gemini":
        return GeminiLLM(tracer)
    raise NotImplementedError(f"provider {CONFIG.llm_provider}")


class FakeLLM:
    """Scripted responses keyed by stage; each value is a model instance or a callable(prompt, images)."""

    def __init__(self, scripts: dict):
        self.scripts, self.calls = scripts, []

    def generate(self, *, stage, system, prompt, schema, images=None, vision=False):
        self.calls.append({"stage": stage, "prompt": prompt, "images": images, "system": system})
        s = self.scripts[stage]
        out = s(prompt, images) if callable(s) else s
        return schema.model_validate(out.model_dump() if isinstance(out, BaseModel) else out)
