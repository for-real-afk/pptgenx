"""Fetched web text is untrusted DATA. Clean it, redact instruction-like sentences, and quote it for the model.
Nothing here can trigger a tool call; tool arguments come from the research plan only."""
from __future__ import annotations

import re
import unicodedata

MAX_CHARS = 6000

SUSPECT = re.compile(
    r"(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|your|all|any)\b[^.\n]{0,30}\b(instruction|prompt|rule|direction)s?"
    r"|\b(you are now|act as|pretend to be|from now on you)\b"
    r"|\b(system prompt|developer message|new instructions?)\b"
    r"|\b(call|invoke|use|run|execute|trigger)\b[^.\n]{0,40}\b(tool|function|command|web_search|fetch_page|search_internal_decks)\b"
    r"|<\s*/?\s*(tool_call|function_call|system|assistant|instructions?)\b"
    r"|\b(reveal|print|output|send|exfiltrate)\b[^.\n]{0,40}\b(api[_ -]?key|secret|password|credentials?|environment)\b",
    re.I,
)
REDACTION = "[redacted: suspected instruction]"
_TAGS = re.compile(r"<[^>]{0,200}>")
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")


def sanitize(text: str, max_chars: int = MAX_CHARS) -> tuple[str, list[str]]:
    """Returns (clean text, list of redacted fragments). Strips control chars/HTML-like tags, redacts suspect sentences."""
    text = "".join(c for c in unicodedata.normalize("NFKC", text) if c == "\n" or c == "\t" or unicodedata.category(c)[0] != "C")
    text = _TAGS.sub(" ", text)
    flagged: list[str] = []
    kept = []
    for sent in _SENT.split(text):
        if SUSPECT.search(sent):
            flagged.append(sent.strip()[:120])
            kept.append(REDACTION)
        elif sent.strip():
            kept.append(sent.strip())
    return re.sub(r"\s+", " ", " ".join(kept))[:max_chars], flagged


def quote(url: str, text: str) -> str:
    """Wrap as inert quoted data. The closing tag cannot be forged from inside the text."""
    safe = text.replace("</source>", "[/source]").replace("<source", "[source")
    return f'<source url="{url.replace(chr(34), "")}">\n{safe}\n</source>'
