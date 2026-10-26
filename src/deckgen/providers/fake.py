"""Scriptable adapter for offline tests: a queue of texts or callables, simulated rate-limit headers, a call log."""
from __future__ import annotations

from deckgen.providers.base import RawResult


class Adapter:
    name = "fake"

    def __init__(self):
        self.script: list = []  # items: str | callable(prompt) -> str | Exception
        self.calls: list[dict] = []
        self.remaining: int | None = None
        self.reset: float | None = None

    def complete(self, *, model, system, prompt, images, temperature, seed, max_tokens, schema_json, cache_prefix=""):
        prompt = cache_prefix + prompt
        self.calls.append({"model": model, "system": system, "prompt": prompt, "temperature": temperature, "seed": seed})
        item = self.script.pop(0) if self.script else "{}"
        if isinstance(item, Exception):
            raise item
        text = item(prompt) if callable(item) else item
        return RawResult(text=text, tokens_in=len(prompt) // 4, tokens_out=len(text) // 4, model=model,
                         remaining_tokens=self.remaining, reset_seconds=self.reset)

    def list_models(self) -> list[str]:
        return ["fake-model"]
