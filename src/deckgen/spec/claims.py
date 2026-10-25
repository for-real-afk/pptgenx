"""Deterministic claim checks: things a slide must not say that its cited findings do not.
Cheap, no LLM, run on every generated slide. They back up (never replace) the LLM faithfulness check.
Each failure is a Violation carrying a registry id; its level (hard or warn) comes from the active gate profile."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from spellchecker import SpellChecker

from deckgen.config import CONFIG
from deckgen.schemas import SlideSpec
from deckgen.schemas.research import Finding
from deckgen.spec.violations import Violation, v

OVERCLAIMS = ["first", "only", "all", "every", "always", "never", "none", "exponential", "exponentially", "unprecedented",
              "unmatched", "unrivaled", "unrivalled", "revolutionary", "dramatic", "dramatically", "greatest", "best",
              "entire", "completely", "instantly", "forever", "undisputed", "unparalleled"]
OVERCLAIM_RE = re.compile(r"(?<![-\w])(" + "|".join(OVERCLAIMS) + r")(?![-\w])", re.I)
NUM_RE = re.compile(r"\d[\d,]*\.?\d*%?")
NAME_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})+\b")
WORD_RE = re.compile(r"\b[a-z]{5,}\b")
TIER_A = {"official", "archive"}
_spell = SpellChecker()
_KNOWN = {"bhp", "homologation", "homologated", "turbocharger", "turbochargers", "supercharger", "drivetrain", "drivetrains",
          "supercar", "supercars", "rallying", "quattro", "lancia", "peugeot", "audi", "toivonen", "cresto", "balestre", "mikkola", "blomqvist", "kankkunen", "salonen"}
NONTEXT = {"id", "section", "layout", "icon", "kind", "type", "source_refs", "source_caption", "speaker_notes", "number_format", "series"}
HEADINGS = {"title", "heading", "year", "value", "name", "header", "categories", "label"}  # names in headings and labels are exempt
ATTRIBUTION = re.compile(r"\b(according to|reportedly|reported|reports?|claims?|claimed|said|says|described|per|as stated by|"
                         r"cited by|suggests?|suggested)\b", re.I)
NUMBER_WORDS = ("hundred", "thousand", "million", "billion", "dozen")


def visible_fields(s: SlideSpec) -> list[tuple[str, str]]:
    """(field name, text) for every piece of text a viewer reads on the slide."""
    out: list[tuple[str, str]] = [("title", s.title)]

    def walk(node, key=""):
        if key in NONTEXT:
            return
        if isinstance(node, str):
            out.append((key, node))
        elif isinstance(node, dict):
            for k, val in node.items():
                walk(val, k)
        elif isinstance(node, list):
            for val in node:
                walk(val, key)

    d = s.model_dump(exclude_none=True)
    for k in ("placeholders", "composition", "chart", "table"):
        if k in d:
            walk(d[k], k if k != "placeholders" else "body")
    return out


def _cited_text(s: SlideSpec, findings: dict[str, Finding]) -> str:
    return " ".join(f"{findings[r].claim} {findings[r].evidence_snippet}" for r in s.source_refs if r in findings)


def _domain_root(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return ".".join(host.split(".")[-2:])


def origin_of(f: Finding) -> str:
    return f.origin_id or _domain_root(f.source_url)


def _is_concrete(text: str) -> bool:
    """A concrete statement carries a number, date or a proper name (a capitalised word that is not the first word)."""
    return bool(re.search(r"\d", text)) or bool(re.search(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-zA-Z]{2,}", text))


def specificity(s: SlideSpec) -> float | None:
    """Share of body statements that are concrete (number, date or name); None when the slide has too few to judge."""
    bodies = [t for f, t in visible_fields(s) if f in ("body", "bullets", "detail", "label") and len(t.split()) >= 4]
    return None if len(bodies) < 3 else sum(_is_concrete(t) for t in bodies) / len(bodies)


def quality_violations(s: SlideSpec, findings: dict[str, Finding]) -> list[Violation]:
    out: list[Violation] = []
    cited = [findings[r] for r in s.source_refs if r in findings]
    low = " ".join(f"{f.claim} {f.evidence_snippet}" for f in cited).lower()
    fields = visible_fields(s)
    if s.composition and s.composition.kind == "timeline":
        for it in s.composition.items:
            if not re.search(r"\d", it.year):
                out.append(v("timeline_label_date", f"timeline label '{it.year}' must be a year or date, not a word; use another composition if there are no dates"))
    sp = specificity(s)
    if sp is not None and sp < CONFIG.specificity_min:
        bodies = [t for f, t in fields if f in ("body", "bullets", "detail", "label") and len(t.split()) >= 4 and not _is_concrete(t)]
        out.append(v("specificity_score", f"specificity {sp:.2f} is below {CONFIG.specificity_min}: '{bodies[0][:50]}' has no number, date or name; "
                                          "use concrete facts from the findings (spare findings are listed in the prompt)"))
    shaky = [f for f in cited if f.needs_review]
    if shaky and not any(ATTRIBUTION.search(t) for _, t in fields):
        out.append(v("attribute_review_claims", f"findings {[f.id for f in shaky]} rest on one low-tier origin: attribute them in the text "
                                                "('X reportedly ...', 'according to <site>') or leave them out"))
    title = s.title.lower()
    body_nums = " ".join(t for f, t in fields if f != "title")
    for w in NUMBER_WORDS:
        if re.search(rf"\b{w}\b", title) and not re.search(rf"\b{w}\b", low):
            out.append(v("title_numbers_supported", f"title says '{w}' but the cited findings do not; state only what the cards and findings support"))
    for tok in re.findall(r"\d[\d,]*", s.title):
        if tok.replace(",", "") not in body_nums.replace(",", "") and tok.replace(",", "") not in low.replace(",", ""):
            out.append(v("title_numbers_supported", f"title number {tok} is not supported by the cards below it or the findings"))
    return out


def _has_run(toks: list[str], snippet: list[str], n: int) -> bool:
    grams = {" ".join(snippet[i:i + n]) for i in range(len(snippet) - n + 1)}
    return any(" ".join(toks[i:i + n]) in grams for i in range(len(toks) - n + 1))


def claim_violations(s: SlideSpec, findings: dict[str, Finding]) -> list[Violation]:
    out: list[Violation] = quality_violations(s, findings)
    cited = _cited_text(s, findings)
    low = cited.lower()
    fields = visible_fields(s)

    # 1. strong wording the findings do not use
    for field, text in fields:
        for m in OVERCLAIM_RE.finditer(text):
            if not re.search(rf"(?<![-\w]){re.escape(m.group(1).lower())}(?![-\w])", low):
                out.append(v("overclaim_word", f"'{m.group(1)}' in '{text[:50]}' overstates: the cited findings never say it (use plain wording)"))
    # 2. numbers on the slide must be stated by the findings (derived figures such as '4%' are not findings)
    known = {n.replace(",", "").rstrip(".") for n in NUM_RE.findall(cited)}
    for field, text in fields:
        if field in ("year", "value", "categories"):
            continue  # stats, years and chart values have their own checks
        for tok in NUM_RE.findall(text):
            n = tok.replace(",", "").rstrip(".")
            bare = n.rstrip("%")
            if bare.isdigit() and len(bare) == 1 and not n.endswith("%"):
                continue
            if n not in known and bare not in known:
                out.append(v("number_in_text_grounded", f"number '{tok}' in '{text[:50]}' is not stated in the cited findings (do not compute or round figures)"))
    # 3. multi-word proper names in body text must come from the findings (headings and labels exempt)
    for field, text in fields:
        if field in HEADINGS:
            continue
        for m in NAME_RE.finditer(text):
            if m.group(0).lower() not in low:
                out.append(v("name_in_body_grounded", f"name '{m.group(0)}' in '{text[:50]}' does not appear in the cited findings"))
    # 4. verbatim copying of source wording: a long run is a hard gate, a shorter run only a warning
    snippets = [re.findall(r"[a-z0-9']+", findings[r].evidence_snippet.lower()) for r in s.source_refs if r in findings]
    for field, text in fields:
        toks = re.findall(r"[a-z0-9']+", text.lower())
        for sn in snippets:
            if _has_run(toks, sn, CONFIG.copy_hard_words):
                out.append(v("copy_long", f"'{text[:50]}' copies {CONFIG.copy_hard_words}+ words of source wording; paraphrase it"))
                break
            if _has_run(toks, sn, CONFIG.copy_warn_words):
                out.append(v("copy_short", f"'{text[:50]}' repeats {CONFIG.copy_warn_words}+ words of source wording; consider paraphrasing"))
                break
    # 5. spelling: unknown words that fewer than two independent origins use are typos until proven otherwise
    seen: set[str] = set()
    for field, text in fields:
        for w in WORD_RE.findall(text):
            if w in seen or w in _KNOWN:
                continue
            seen.add(w)
            if _spell.unknown([w]):
                roots = {origin_of(f) for f in findings.values() if w in f"{f.claim} {f.evidence_snippet}".lower()}
                if len(roots) < 2:
                    out.append(v("spelling", f"'{w}' looks misspelled (not a dictionary word and not used by two independent origins)"))
    # 6. numbers need independent support: one authoritative origin or two independent origins (a vendor cannot vouch for itself)
    slide_nums = any(len(t.rstrip("%").replace(",", "")) >= 2 for _, tx in fields for t in NUM_RE.findall(tx))
    is_stat = bool(s.chart or (s.composition and s.composition.kind == "stats"))
    if (is_stat or slide_nums) and s.layout not in ("TITLE", "SECTION", "SOURCES") and s.source_refs:
        cs = [findings[r] for r in s.source_refs if r in findings]
        origins = {origin_of(f) for f in cs if not f.self_sourced}
        if len(origins) < 2 and not any(f.tier >= 4 or f.source_type in TIER_A for f in cs):
            out.append(v("numbers_need_two_origins_stat" if is_stat else "numbers_need_two_origins_text",
                         f"numbers need two independent origins or one authoritative origin; cited origins: {sorted(origins) or 'only self-sourced pages'}. "
                         "Cite other supporting findings or remove the figures (a vendor alone cannot support claims about itself)"))
    return out


def claim_errors(s: SlideSpec, findings: dict[str, Finding]) -> list[str]:
    """All messages, hard and warn (kept for tests and simple callers)."""
    return [x.message for x in claim_violations(s, findings)]
