"""Groq (OpenAI-compatible). All Groq-specific behaviour is here: reasoning flags per model family, structured-output mode,
rate-limit headers. Correctness never depends on provider-side schema enforcement: the caller validates with Pydantic.
Note: Groq does not support streaming or tool use together with structured outputs; this adapter uses neither."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import httpx

from deckgen.providers.base import ProviderUnavailable, RateLimited, RawResult, parse_duration

BASE = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
REASONING_EFFORT = os.environ.get("GROQ_REASONING_EFFORT", "low")  # gpt-oss family: low | medium | high
STRICT = os.environ.get("GROQ_STRICT_SCHEMA", "") == "1"            # best-effort json_object unless explicitly strict
STRICT_MODELS = ("openai/gpt-oss", "meta-llama/llama-4")            # families documented to support json_schema
VISION_MODELS = ("meta-llama/llama-4",)


def _model_params(model: str) -> dict:
    """Per-family request parameters: reasoning models must not leak chain-of-thought into the JSON."""
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": REASONING_EFFORT, "include_reasoning": False}
    if "qwen3" in model or "deepseek-r1" in model:
        return {"reasoning_format": "hidden"}
    return {}


class Adapter:
    name = "groq"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"}

    def complete(self, *, model, system, prompt, images, temperature, seed, max_tokens, schema_json):
        content: list | str = prompt
        if images:
            if not model.startswith(VISION_MODELS):
                raise ValueError(f"model {model} on Groq has no vision support; point the critic role at a vision model")
            content = [{"type": "text", "text": prompt}] + [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(Path(i).read_bytes()).decode()}}
                for i in images]
        body = {"model": model, "temperature": temperature, "max_completion_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                **_model_params(model)}
        if seed is not None:
            body["seed"] = seed
        if STRICT and schema_json and model.startswith(STRICT_MODELS) and not images:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "out", "schema": json.loads(schema_json), "strict": False}}
        else:
            body["response_format"] = {"type": "json_object"}  # best effort; the caller validates
        try:
            r = httpx.post(f"{BASE}/chat/completions", headers=self._headers(), json=body, timeout=120)
        except httpx.HTTPError as e:
            raise ProviderUnavailable(str(e)[:200]) from e
        h = r.headers
        if r.status_code == 429:
            ra = float(h.get("retry-after", 0) or 0) or parse_duration(h.get("x-ratelimit-reset-tokens")) or 10.0
            daily = "per day" in r.text.lower() or "tokens per day" in r.text.lower()
            raise RateLimited(ra, daily=daily, message=r.text[:200])
        if r.status_code >= 500:
            raise ProviderUnavailable(f"{r.status_code} {r.text[:150]}")
        if r.status_code == 400 and "json_validate_failed" in r.text:
            # invalid JSON in json_object mode: hand the failed text back as a normal (invalid) result so the caller can repair it
            return RawResult(text=(r.json().get("error", {}).get("failed_generation") or ""), model=model)
        r.raise_for_status()
        j = r.json()
        u = j.get("usage", {})
        rem = h.get("x-ratelimit-remaining-tokens")
        return RawResult(text=j["choices"][0]["message"].get("content") or "", tokens_in=u.get("prompt_tokens", 0),
                         tokens_out=u.get("completion_tokens", 0), model=j.get("model", model),
                         remaining_tokens=int(rem) if rem and rem.isdigit() else None,
                         reset_seconds=parse_duration(h.get("x-ratelimit-reset-tokens")),
                         raw_headers={k: v for k, v in h.items() if k.lower().startswith("x-ratelimit")})

    def list_models(self) -> list[str]:
        r = httpx.get(f"{BASE}/models", headers=self._headers(), timeout=30)
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]
