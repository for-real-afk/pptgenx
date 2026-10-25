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

    def complete(self, *, model, system, prompt, images, temperature, seed, max_tokens, schema_json):
        content: list = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                      "data": base64.b64encode(Path(i).read_bytes()).decode()}} for i in images or []]
        content.append({"type": "text", "text": prompt})
        body = {"model": model, "max_tokens": max_tokens, "temperature": temperature, "system": system,
                "messages": [{"role": "user", "content": content}]}  # seed is not supported by this API: ignored
        try:
            r = httpx.post(f"{BASE}/v1/messages", headers=self._headers(), json=body, timeout=180)
        except httpx.HTTPError as e:
            raise ProviderUnavailable(str(e)[:200]) from e
        h = r.headers
        if r.status_code == 429:
            raise RateLimited(float(h.get("retry-after", 10) or 10), daily=False, message=r.text[:200])
        if r.status_code >= 500:
            raise ProviderUnavailable(f"{r.status_code} {r.text[:150]}")
        r.raise_for_status()
        j = r.json()
        text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
        u = j.get("usage", {})
        rem = h.get("anthropic-ratelimit-tokens-remaining")
        return RawResult(text=text, tokens_in=u.get("input_tokens", 0), tokens_out=u.get("output_tokens", 0), model=j.get("model", model),
                         remaining_tokens=int(rem) if rem and rem.isdigit() else None)

    def list_models(self) -> list[str]:
        r = httpx.get(f"{BASE}/v1/models", headers=self._headers(), timeout=30)
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]
