"""Photos from Wikimedia Commons (free API, no key). Only openly licensed files (CC BY, CC BY-SA, CC0, public domain) are accepted, and the licence,
author and file page are kept for the on-slide credit. The model never writes a caption: it only picks a candidate id for a slide; the credit comes from
Commons metadata. Whether a photo really shows what its title says is NOT verified here: every photo slide is listed in review_before_use.md."""
from __future__ import annotations

import html
import io
import os
import re
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from deckgen.llm import LLM

NL = chr(10)
API = "https://commons.wikimedia.org/w/api.php"


def user_agent() -> dict | None:
    """Wikimedia requires a descriptive User-Agent with a real contact. The contact comes only from DECKGEN_CONTACT; unset = no photos."""
    contact = os.environ.get("DECKGEN_CONTACT", "").strip()
    return {"User-Agent": f"deckgen/0.1 ({contact}) python-requests"} if contact else None
OK_LICENSE = re.compile(r"^(CC[ -]BY(?![ -]N)|CC0|Public domain|PD)", re.I)
MIN_WIDTH = 900
PER_QUERY = 6
PHOTO_BOX = (7.6, 4.85)  # inches; keep in step with PHOTO_TEXT in brand layouts.js
QUERY_SYSTEM = "You write image search queries for Wikimedia Commons. Output JSON only."


class Candidate(BaseModel):
    id: str
    title: str
    desc: str = ""
    url: str
    license: str
    author: str = ""
    page: str = ""
    for_slide: int = 0


class _Q(BaseModel):
    model_config = ConfigDict(extra="ignore")
    n: int
    queries: list[str]


class _Queries(BaseModel):
    model_config = ConfigDict(extra="ignore")
    slides: list[_Q]


def _plain(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def search_commons(query: str, limit: int = PER_QUERY, get=httpx.get, headers: dict | None = None) -> list[dict]:
    r = get(API, headers=headers or user_agent(), timeout=30, params={
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": limit,
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1600})
    r.raise_for_status()
    return sorted((r.json().get("query", {}).get("pages", {})).values(), key=lambda p: p.get("index", 0))


def to_candidate(page: dict, cid: str, slide: int) -> Candidate | None:
    info = (page.get("imageinfo") or [None])[0]
    if not info or info.get("mime") not in ("image/jpeg", "image/png") or info.get("width", 0) < MIN_WIDTH:
        return None
    md = info.get("extmetadata", {})
    lic = _plain(md.get("LicenseShortName", {}).get("value", ""))
    if not OK_LICENSE.match(lic) or _plain(md.get("Restrictions", {}).get("value", "")):
        return None
    author = _plain(md.get("Artist", {}).get("value", ""))[:50]
    if not author or author.lower() == "unknown":
        return None  # attribution is part of the licence: no readable author, no photo
    title = re.sub(r"\.\w+$", "", page["title"].removeprefix("File:")).replace("_", " ")
    return Candidate(id=cid, title=title, desc=_plain(md.get("ImageDescription", {}).get("value", ""))[:160], url=info.get("thumburl") or info["url"],
                     license=lic, author=author, page=info.get("descriptionurl", ""), for_slide=slide)


def find_for_outline(llm: LLM, brief, outline, get=httpx.get, log: list | None = None) -> dict[int, list[Candidate]]:
    """One cheap call writes 1-2 short queries per content slide; Commons does the search. Candidate ids are unique across the deck."""
    slides = [o for o in outline.slides if o.layout_hint not in ("TITLE", "CLOSING", "SECTION", "SOURCES")]
    listing_ = NL.join(f"{o.n}. {o.key_message} ({o.section})" for o in slides)
    qs = llm.generate(stage="image_queries", system=QUERY_SYSTEM, schema=_Queries,
                      prompt=f"Deck topic: {brief.topic}{NL}For each slide write 1-2 short Commons search queries (2-5 words, concrete named things: a specific "
                             f"vehicle, person, place or event) that would find a real photograph illustrating it. Skip slides about abstractions.{NL}{listing_}")
    out: dict[int, list[Candidate]] = {}
    seen: set[str] = set()
    k = 0
    for q in qs.slides:
        for query in q.queries[:2]:
            try:
                pages = search_commons(query, get=get)
            except httpx.HTTPError as e:  # loud: console now, photos.json and the run report later
                code = getattr(getattr(e, "response", None), "status_code", None)
                msg = f"photos: Commons search '{query}' failed ({code or type(e).__name__})"
                print(msg)
                if log is not None:
                    log.append({"query": query, "ok": False, "error": msg})
                continue
            if log is not None:
                log.append({"query": query, "ok": True, "results": len(pages)})
            for pg in pages:
                if pg.get("title") in seen:
                    continue
                c = to_candidate(pg, f"p{k + 1:02d}", q.n)
                if c:
                    seen.add(pg["title"])
                    k += 1
                    out.setdefault(q.n, []).append(c)
    return {n: cs[:4] for n, cs in out.items()}


def listing(cands: list[Candidate]) -> str:
    return NL.join(f"{c.id}: {c.title}" + (f" - {c.desc}" if c.desc else "") for c in cands)


def credit(c: Candidate) -> str:
    return f"Photo: {c.author}, {c.license}, Wikimedia Commons"


def fetch(c: Candidate, dest_dir: Path, get=httpx.get) -> Path:
    """Download, centre-crop to the photo box aspect, save as JPEG (the renderer places it as is)."""
    from PIL import Image

    dest = dest_dir / f"{c.id}.jpg"
    if dest.exists():
        return dest
    r = get(c.url, headers=user_agent(), timeout=60, follow_redirects=True)
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content)).convert("RGB")
    want = PHOTO_BOX[0] / PHOTO_BOX[1]
    w, h = im.size
    if w / h > want:
        nw = int(h * want)
        im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = int(w / want)
        im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    dest_dir.mkdir(parents=True, exist_ok=True)
    im.resize((1520, int(1520 / want))).save(dest, "JPEG", quality=85)
    return dest


class Catalog:
    """Candidate photos per outline slide; a photo is used at most once per deck."""

    def __init__(self, cands: dict[int, list[Candidate]], dest_dir: Path, log: list | None = None):
        self.cands, self.dest_dir, self.used, self.log = cands, dest_dir, set(), log if log is not None else []

    def offered(self, n: int) -> list[Candidate]:
        return [c for c in self.cands.get(n, []) if c.id not in self.used]

    def take(self, cid: str, n: int, get=httpx.get):
        """(Photo, Candidate) for an offered id, or None. Downloads and crops the file."""
        from deckgen.schemas.deck import Photo

        c = next((c for c in self.offered(n) if c.id == cid), None)
        if c is None:
            return None
        try:
            path = str(fetch(c, self.dest_dir, get))
        except Exception as e:  # noqa: BLE001 - reported, never swallowed; the slide simply gets another layout
            msg = f"photos: download of {c.id} ({c.title}) failed ({type(e).__name__}: {str(e)[:80]})"
            print(msg)
            self.log.append({"photo": c.id, "ok": False, "error": msg})
            return None
        self.used.add(c.id)
        self.log.append({"photo": c.id, "ok": True, "title": c.title, "credit": credit(c), "page": c.page})
        return Photo(path=path, alt=c.title[:120], credit=credit(c)), c

    def to_json(self) -> str:
        import json

        return json.dumps({"log": self.log, "candidates": {str(n): [c.model_dump() for c in cs] for n, cs in self.cands.items()}}, indent=1)

    @classmethod
    def from_json(cls, text: str, dest_dir: Path) -> Catalog:
        import json

        d = json.loads(text)
        return cls({int(n): [Candidate(**c) for c in cs] for n, cs in d.get("candidates", {}).items()}, dest_dir, d.get("log", []))
