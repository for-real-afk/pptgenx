"""Adapter registry: get_adapter("groq") imports deckgen.providers.groq and builds its Adapter. Adding a provider = adding a file."""
from __future__ import annotations

import importlib

_cache: dict[str, object] = {}


def get_adapter(name: str):
    if name not in _cache:
        try:
            mod = importlib.import_module(f"deckgen.providers.{name}")
        except ModuleNotFoundError as e:
            raise ValueError(f"unknown provider '{name}': there is no file deckgen/providers/{name}.py") from e
        _cache[name] = mod.Adapter()
    return _cache[name]


def register(name: str, adapter) -> None:
    """Tests and experiments can inject an adapter object."""
    _cache[name] = adapter
