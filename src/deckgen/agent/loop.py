"""The agent loop: plain Messages API tool use (httpx, no SDK). One model sees the skill file, the layout manifest and the tool definitions as a cached
prefix, calls tools, looks at rendered slide images, and ends by calling `finish`. Before every call the cost ledger estimates the worst case
(counted input + max_tokens at the output price, images counted as input) and stops the run cleanly if it would pass the budget. Old tool results
and old images are trimmed so the context stays small; the agent's own notes.md carries what matters."""
from __future__ import annotations

import copy
import json
import os
import time

import httpx

from deckgen.agent.phases import WRAPUP_TURNS
from deckgen.providers.base import ProviderUnavailable

NL = chr(10)
BASE = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
VERSION = "2023-06-01"
MAX_TOKENS = 20_000
KEEP_RECENT_RESULTS = 8          # tool results kept in full; older bulky ones are trimmed
BULKY = ("read_source", "fetch_page", "summarize_sources", "read_file", "search")
TRIM_TO = 300
NET_TRIES = 5
LIMIT_WARNING = 0.85         # share of the tool-call limit after which the agent is told to wrap up


def anthropic_transport(body: dict) -> dict:
    """POST /v1/messages. Retries 429 and 5xx with a wait; any other error becomes an exception carrying the API's message."""
    headers = {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": VERSION}
    last = ""
    for k in range(1, NET_TRIES + 1):
        try:
            r = httpx.post(f"{BASE}/v1/messages", headers=headers, json=body, timeout=300)
        except httpx.HTTPError as e:
            last = str(e)[:200]
            time.sleep(min(30, 2 ** k))
            continue
        if r.status_code == 429:
            time.sleep(min(60, float(r.headers.get("retry-after", 10) or 10)))
            last = "rate limited"
            continue
        if r.status_code >= 500:
            last = f"{r.status_code} {r.text[:150]}"
            time.sleep(min(30, 2 ** k))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Anthropic API {r.status_code}: {r.text[:400]}")
        return r.json()
    raise ProviderUnavailable(f"Anthropic API unavailable after {NET_TRIES} tries: {last}")


def flatten(system: list[dict], tools: list[dict], messages: list[dict]) -> tuple[str, str, int]:
    """(system+tools text, messages text, image count) for the budget guard; image bytes are not text, they are counted per image."""
    sys_text = NL.join(b.get("text", "") for b in system) + json.dumps(tools)
    parts, images = [], 0

    def walk(c):
        nonlocal images
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for b in c:
                walk(b)
        elif isinstance(c, dict):
            t = c.get("type")
            if t == "image":
                images += 1
            elif t == "text":
                parts.append(c.get("text", ""))
            elif t == "tool_result":
                walk(c.get("content", ""))
            elif t == "tool_use":
                parts.append(json.dumps(c.get("input", {})))
            elif t == "thinking":
                parts.append(c.get("thinking", ""))

    for m in messages:
        walk(m["content"])
    return sys_text, NL.join(parts), images


def trim(messages: list[dict], names: dict[str, str]) -> list[dict]:
    """Drop images from earlier renders (keep the latest set) and shrink old bulky tool results. The first message is never touched."""
    msgs = copy.deepcopy(messages)
    for m in msgs:   # thinking blocks carry signatures bound to the exact request that produced them; replaying them is rejected, so history keeps only text and tool calls
        if m["role"] == "assistant" and isinstance(m["content"], list):
            m["content"] = [b for b in m["content"] if b.get("type") not in ("thinking", "redacted_thinking")] or [{"type": "text", "text": "(working)"}]
    result_msgs = [i for i, m in enumerate(msgs) if i and m["role"] == "user" and isinstance(m["content"], list)
                   and any(b.get("type") == "tool_result" for b in m["content"])]
    with_images = [i for i in result_msgs if any(isinstance(b.get("content"), list) and any(x.get("type") == "image" for x in b["content"]) for b in msgs[i]["content"])]
    for i in with_images[:-1]:
        for b in msgs[i]["content"]:
            if isinstance(b.get("content"), list):
                b["content"] = [x if x.get("type") != "image" else {"type": "text", "text": "[image of an earlier build removed]"} for x in b["content"]]
    for i in result_msgs[:-KEEP_RECENT_RESULTS]:
        for b in msgs[i]["content"]:
            if b.get("type") == "tool_result" and names.get(b.get("tool_use_id")) in BULKY and isinstance(b.get("content"), str) and len(b["content"]) > TRIM_TO * 2:
                b["content"] = b["content"][:TRIM_TO] + " ...[trimmed: what mattered is in notes.md; the saved page can be read again]"
    return msgs


class AgentLoop:
    def __init__(self, tools, ledger, phases, *, model: str, system_text: str, tool_defs: list[dict], max_calls: int, max_seconds: int,
                 transport=anthropic_transport, approval=None, max_tokens: int = MAX_TOKENS):
        self.tools, self.ledger, self.phases, self.model, self.transport, self.approval = tools, ledger, phases, model, transport, approval
        self.max_calls, self.max_seconds, self.max_tokens = max_calls, max_seconds, max_tokens
        self.system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
        self.tool_defs = copy.deepcopy(tool_defs)
        self.tool_defs[-1]["cache_control"] = {"type": "ephemeral"}      # skill + manifest + tools are the stable cached prefix
        self.messages: list[dict] = []
        self.names: dict[str, str] = {}
        self.tool_calls = 0
        self.model_calls = 0
        self.stop_reason = ""
        self.wrapup_turns = 0

    def _body(self) -> dict:
        msgs = trim(self.messages, self.names)
        last = msgs[-1]["content"]
        if isinstance(last, str):
            msgs[-1]["content"] = last = [{"type": "text", "text": last}]
        last[-1]["cache_control"] = {"type": "ephemeral"}                # rolling breakpoint on the newest block
        body = {"model": self.model, "max_tokens": self.max_tokens, "system": self.system, "tools": self.tool_defs, "messages": msgs}
        if "sonnet" in self.model:
            body["thinking"] = {"type": "between_tools"}                 # see providers/anthropic.py
        return body

    def _turn(self) -> dict:
        body = self._body()
        sys_text, msg_text, images = flatten(self.system, self.tool_defs, body["messages"])
        self.ledger.stage = self.phases.phase
        self.ledger.check(self.model, sys_text, msg_text, self.max_tokens, images)         # raises BudgetAbort before any spend that could pass the cap
        t0 = time.time()
        try:
            resp = self.transport(body)
        except Exception as e:  # noqa: BLE001 - recorded as a failed call, then re-raised
            self.ledger.record(role="agent", llm_stage="agent_turn", model=self.model, tin=0, tout=0, latency_s=time.time() - t0, cache="miss",
                               outcome="failed", error=f"{type(e).__name__}: {str(e)[:300]}")
            raise
        u = resp.get("usage", {})
        self.model_calls += 1
        self.ledger.record(role="agent", llm_stage="agent_turn", model=self.model, tin=u.get("input_tokens", 0), tout=u.get("output_tokens", 0),
                           cw=u.get("cache_creation_input_tokens", 0) or 0, cr=u.get("cache_read_input_tokens", 0) or 0, latency_s=time.time() - t0,
                           cache="miss", outcome="ok" if resp.get("stop_reason") != "max_tokens" else "truncated",
                           context_tokens=u.get("input_tokens", 0) + (u.get("cache_creation_input_tokens", 0) or 0) + (u.get("cache_read_input_tokens", 0) or 0))
        return resp

    def run(self, task: str) -> dict:
        self.messages = [{"role": "user", "content": task}]
        t_start = time.time()
        while True:
            if self.tools.finished:
                self.stop_reason = "finished"
                break
            if self.tool_calls >= self.max_calls:
                self.stop_reason = f"tool call limit ({self.max_calls})"
                break
            if time.time() - t_start > self.max_seconds:
                self.stop_reason = f"wall clock limit ({self.max_seconds}s)"
                break
            if self.tool_calls >= self.max_calls * LIMIT_WARNING and self.phases.advance("wrapup", "tool call limit nearly reached"):
                self.tools.start_wrapup()
                self.messages.append({"role": "user", "content": "WRAP-UP: the tool call limit is nearly reached. Do not build or render again. Write review.md (including every "
                                                                 "defect you saw but did not fix) and call finish now."})
            if self.phases.phase == "wrapup":
                self.wrapup_turns += 1
                if self.wrapup_turns > WRAPUP_TURNS:
                    self.stop_reason = "the agent did not finish within the wrap-up turns"
                    break
            resp = self._turn()
            content = resp.get("content", [])
            truncated = resp.get("stop_reason") == "max_tokens"
            if truncated:                                                # an unfinished tool call must not be executed
                content = [b for b in content if b.get("type") != "tool_use"] or [{"type": "text", "text": "(cut off)"}]
            self.messages.append({"role": "assistant", "content": content})
            uses = [b for b in content if b.get("type") == "tool_use"]
            if truncated:
                self.messages.append({"role": "user", "content": "Your last response was cut off at the output limit, so nothing was executed. Write large files in parts with write_file(append=true), then continue."})
                continue
            if not uses:
                nudge = self.approval() if self.approval else None
                if nudge:
                    self.messages.append({"role": "user", "content": nudge})
                    continue
                self.stop_reason = "the agent stopped without calling finish"
                break
            results = []
            for b in uses:
                self.names[b["id"]] = b["name"]
                self.tool_calls += 1
                out, is_err = self.tools.call(b["name"], b.get("input", {}))
                results.append({"type": "tool_result", "tool_use_id": b["id"], "content": out, **({"is_error": True} if is_err else {})})
            for note in self.phases.review(self.ledger):          # a phase that used its money ends here and hands over
                results.append({"type": "text", "text": note})
            if self.phases.phase == "wrapup":
                self.tools.start_wrapup()
            self.messages.append({"role": "user", "content": results})
        if self.tools.finished and self.stop_reason == "finished":
            return {"stop_reason": "finished", "tool_calls": self.tool_calls, "model_calls": self.model_calls, "seconds": round(time.time() - t_start)}
        return {"stop_reason": self.stop_reason, "tool_calls": self.tool_calls, "model_calls": self.model_calls, "seconds": round(time.time() - t_start)}
