"""Search + fetch behind interfaces, with a disk cache (reproducible evals, no repeat Tavily spend) and hard caps."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Protocol

import httpx
from lxml import html as lhtml
from pydantic import BaseModel

from deckgen.config import CONFIG

CACHE_DIR = CONFIG.runs_dir / "_cache"
UA = "Mozilla/5.0 (compatible; deckgen-research/0.1)"


class BudgetExhausted(RuntimeError):
    pass


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""
    content: str = ""  # page text when the provider returns it
    score: float = 0.0


class SearchProvider(Protocol):
    def search(self, query: str, max_results: int) -> list[SearchResult]: ...


class PageFetcher(Protocol):
    def fetch(self, url: str) -> str: ...


class Budget:
    """Per-run hard caps. Cache hits are free for API caps but still count as tool calls."""

    def __init__(self, max_tool_calls: int, max_pages: int, max_seconds: int, max_api_calls: int):
        self.max_tool_calls, self.max_pages, self.max_api_calls = max_tool_calls, max_pages, max_api_calls
        self.deadline = time.time() + max_seconds
        self.tool_calls = self.pages = self.api_calls = self.cache_hits = 0

    def spend(self, kind: str, cached: bool) -> None:
        if time.time() > self.deadline:
            raise BudgetExhausted("wall-clock budget")
        if self.tool_calls >= self.max_tool_calls:
            raise BudgetExhausted("tool-call budget")
        if kind == "fetch_page" and self.pages >= self.max_pages:
            raise BudgetExhausted("page budget")
        if not cached and self.api_calls >= self.max_api_calls:
            raise BudgetExhausted("external API call cap")
        self.tool_calls += 1
        self.pages += kind == "fetch_page"
        self.cache_hits += cached
        self.api_calls += not cached

    def stats(self) -> dict[str, int]:
        return {"tool_calls": self.tool_calls, "pages": self.pages, "api_calls": self.api_calls, "cache_hits": self.cache_hits}


def _key(tool: str, **args) -> Path:
    h = hashlib.sha256(json.dumps([tool, args], sort_keys=True).encode()).hexdigest()[:24]
    return CACHE_DIR / tool / f"{h}.json"


class TavilySearch:
    def __init__(self, api_key: str | None = None):
        self.key = api_key or os.environ["TAVILY_API_KEY"]

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        r = httpx.post("https://api.tavily.com/search", timeout=40,
                       headers={"Authorization": f"Bearer {self.key}"},
                       json={"query": query, "max_results": max_results, "search_depth": "basic", "include_raw_content": "text"})
        r.raise_for_status()
        return [SearchResult(title=x.get("title", ""), url=x["url"], snippet=x.get("content", ""),
                             content=(x.get("raw_content") or "")[:20000], score=x.get("score", 0.0))
                for x in r.json().get("results", [])]


class HttpFetcher:
    def fetch(self, url: str) -> str:
        r = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": UA})
        r.raise_for_status()
        if "html" not in r.headers.get("content-type", "html"):
            return ""
        doc = lhtml.fromstring(r.content[:400_000])
        for bad in doc.xpath("//script|//style|//nav|//footer|//header|//aside|//form"):
            bad.getparent().remove(bad)
        return " ".join(doc.text_content().split())


class ResearchTools:
    """The only door to the outside world. Arguments come from the research plan, never from page text."""

    def __init__(self, search: SearchProvider, fetcher: PageFetcher, budget: Budget, log: list | None = None):
        self._search, self._fetch, self.budget = search, fetcher, budget
        self.log = log if log is not None else []  # (tool, args) for every call: tests assert on this

    def _cached(self, tool: str, args: dict, fn):
        path = _key(tool, **args)
        hit = path.exists()
        self.budget.spend(tool, hit)
        self.log.append((tool, args))
        if hit:
            return json.loads(path.read_text(encoding="utf-8"))
        out = fn()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out), encoding="utf-8")
        return out

    def web_search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        raw = self._cached("web_search", {"query": query, "n": max_results},
                           lambda: [r.model_dump() for r in self._search.search(query, max_results)])
        return [SearchResult(**r) for r in raw]

    def fetch_page(self, url: str) -> str:
        return self._cached("fetch_page", {"url": url}, lambda: self._fetch.fetch(url))
