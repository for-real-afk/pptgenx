"""Provider adapter contract. A provider is ONE file in deckgen/providers/ exposing `Adapter`; config names it.
Everything provider-specific (reasoning flags, structured-output mode, header names, model ids) lives in that file."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class RawResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    # rate-limit state the provider reported, normalised: remaining tokens and seconds until reset (None = unknown)
    cache_write_tokens: int = 0  # provider prompt cache: tokens written to / read from the cache (billed at their own prices)
    cache_read_tokens: int = 0
    remaining_tokens: int | None = None
    reset_seconds: float | None = None
    raw_headers: dict = field(default_factory=dict)


class RateLimited(Exception):
    """HTTP 429. `daily` means the per-day quota is gone (waiting a minute will not help)."""

    def __init__(self, retry_after: float = 5.0, daily: bool = False, message: str = ""):
        super().__init__(message or f"rate limited, retry after {retry_after}s")
        self.retry_after, self.daily = retry_after, daily


class ProviderUnavailable(Exception):
    """5xx or network trouble: retry with backoff."""


class Adapter(Protocol):
    name: str

    def complete(self, *, model: str, system: str, prompt: str, images: list[Path] | None, temperature: float,
                 seed: int | None, max_tokens: int, schema_json: str | None, cache_prefix: str = "") -> RawResult: ...

    def list_models(self) -> list[str]: ...


def parse_duration(s: str | None) -> float | None:
    """Rate-limit reset strings such as 2m59.56s, 7.66s, 450ms or 1h2m, as seconds."""
    if not s:
        return None
    total, found = 0.0, False
    for num, unit in re.findall(r"([\d.]+)(ms|h|m|s)", s):
        found = True
        total += float(num) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total if found else None
