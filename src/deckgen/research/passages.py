"""Full-page handling: split a page into passages, pick the ones relevant to a research question (lexical BM25, no model needed),
and fingerprint the page (MinHash) so mirrors and republished copies can be recognised as one origin."""
from __future__ import annotations

import math
import random
import re
import zlib
from collections import Counter

STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "as", "by", "at", "from", "that", "this", "these", "those", "is", "are", "was", "were", "be", "been", "it", "its", "their", "his", "her", "which", "who", "whom", "what", "when", "where", "how", "not", "no", "but", "if", "than", "then", "also", "into", "over", "under", "about", "after", "before", "during", "between"}
_P = (1 << 61) - 1
_rng = random.Random(1234)
_PERMS = [(_rng.randrange(1, _P), _rng.randrange(0, _P)) for _ in range(32)]


def words(t: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in STOP]


def chunk(text: str, size: int = 1100) -> list[str]:
    """Greedy sentence-boundary chunks of about `size` characters."""
    sents = re.split(r"(?<=[.!?])\s+", text)
    out, cur = [], ""
    for s in sents:
        if cur and len(cur) + len(s) > size:
            out.append(cur)
            cur = ""
        cur += (" " if cur else "") + s
    if cur:
        out.append(cur)
    return [c for c in out if len(c) > 60]


def select(text: str, question: str, k: int = 3, size: int = 1100) -> list[str]:
    """The k passages that best match the question (BM25), returned in page order."""
    chunks = chunk(text, size)
    if len(chunks) <= k:
        return chunks
    docs = [Counter(words(c)) for c in chunks]
    q = set(words(question))
    n, avg = len(docs), sum(sum(d.values()) for d in docs) / len(docs)
    df = {t: sum(1 for d in docs if t in d) for t in q}
    scores = []
    for i, d in enumerate(docs):
        ln, s = sum(d.values()), 0.0
        for t in q:
            if t in d:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * d[t] * 2.2 / (d[t] + 1.2 * (0.25 + 0.75 * ln / avg))
        scores.append((s, i))
    best = sorted(i for _, i in sorted(scores, reverse=True)[:k])
    return [chunks[i] for i in best]


def signature(text: str) -> list[int]:
    """MinHash over 5-word shingles; similar pages share many signature slots."""
    toks = re.findall(r"[a-z0-9]+", text.lower())
    shingles = {zlib.crc32(" ".join(toks[i:i + 5]).encode()) for i in range(max(len(toks) - 4, 0))}
    if not shingles:
        return []
    shingles = set(list(shingles)[:20000])
    return [min((a * h + b) % _P for h in shingles) for a, b in _PERMS]


def similarity(a: list[int], b: list[int]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x == y for x, y in zip(a, b)) / len(a)
