"""Anthropic Messages API adapter (plain HTTP, no SDK). Ready for the team key: set ANTHROPIC_API_KEY and point a role at
anthropic:<model id>. No pipeline code changes. NOT exercised live yet: there is no key in this environment."""
from __future__ import annotations

import base64
import os
from pathlib import Path

import httpx

from deckgen.providers.base import ProviderUnavailable, RateLimited, RawResult

BASE = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
VERSION = "2023-06-01"


class Adapter:
    name = "anthropic"

    def _headers(self) -> dict:
        return {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": VERSION}

    def complete(self, *, model, system, prompt, images, temperature, seed, max_tokens, schema_json, cache_prefix=""):
        content: list = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                      "data": base64.b64encode(Path(i).read_bytes()).decode()}} for i in images or []]
        if cache_prefix:  # stable material first, marked for prompt caching; the variable part follows it
            content.append({"type": "text", "text": cache_prefix, "cache_control": {"type": "ephemeral"}})
        content.append({"type": "text", "text": prompt})
        sys_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        body = {"model": model, "max_tokens": max_tokens, "system": sys_blocks,
                "messages": [{"role": "user", "content": content}]}
        if "sonnet" in model:  # Sonnet thinks by default and the hidden tokens eat max_tokens (empty text); Haiku rejects the field
            body["thinking"] = {"type": "between_tools"}  # seed and temperature are not accepted by the current models (400 'temperature is deprecated'): both ignored
        try:
            r = httpx.post(f"{BASE}/v1/messages", headers=self._headers(), json=body, timeout=180)
        except httpx.HTTPError as e:
            raise ProviderUnavailable(str(e)[:200]) from e
        h = r.headers
        if r.status_code == 429:
            raise RateLimited(float(h.get("retry-after", 10) or 10), daily=False, message=r.text[:200])
        if r.status_code >= 500:
            raise ProviderUnavailable(f"{r.status_code} {r.text[:150]}")
        if r.status_code >= 400:
            raise RuntimeError(f"Anthropic API {r.status_code}: {r.text[:400]}")
        j = r.json()
        text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
        u = j.get("usage", {})
        rem = h.get("anthropic-ratelimit-tokens-remaining")
        return RawResult(text=text, tokens_in=u.get("input_tokens", 0), tokens_out=u.get("output_tokens", 0), model=j.get("model", model),
                         stop_reason=j.get("stop_reason") or "", cache_write_tokens=u.get("cache_creation_input_tokens", 0) or 0, cache_read_tokens=u.get("cache_read_input_tokens", 0) or 0,
                         remaining_tokens=int(rem) if rem and rem.isdigit() else None)

    def list_models(self) -> list[str]:
        r = httpx.get(f"{BASE}/v1/models", headers=self._headers(), timeout=30)
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]
