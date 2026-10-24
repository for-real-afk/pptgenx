"""Deterministic claim checks: things a slide must not say that its cited findings do not.
Cheap, no LLM, run on every generated slide. They back up (never replace) the LLM faithfulness check."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from spellchecker import SpellChecker

from deckgen.schemas import SlideSpec
from deckgen.schemas.research import Finding

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
HEADINGS = {"title", "heading", "year", "value", "name", "header", "categories"}


def visible_fields(s: SlideSpec) -> list[tuple[str, str]]:
    """(field name, text) for every piece of text a viewer reads on the slide."""
    out: list[tuple[str, str]] = [("title", s.title)]

    def walk(node, key=""):
        if key in NONTEXT:
            return
        if isinstance(node, str):
            out.append((key, node))
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)

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


ATTRIBUTION = re.compile(r"\b(according to|reportedly|reported|reports?|claims?|claimed|said|says|described|per|as stated by|"
                         r"cited by|suggests?|suggested)\b", re.I)
NUMBER_WORDS = ("hundred", "thousand", "million", "billion", "dozen")


def _is_concrete(text: str) -> bool:
    """A concrete statement carries a number, date or a proper name (a capitalised word that is not the first word)."""
    return bool(re.search(r"\d", text)) or bool(re.search(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-zA-Z]{2,}", text))


def quality_errors(s: SlideSpec, findings: dict[str, Finding]) -> list[str]:
    """Slide-quality rules that grounding checks reward the opposite of: vagueness, unattributed shaky claims, loose titles."""
    errs: list[str] = []
    cited = [findings[r] for r in s.source_refs if r in findings]
    low = " ".join(f"{f.claim} {f.evidence_snippet}" for f in cited).lower()
    fields = visible_fields(s)
    if s.composition and s.composition.kind == "timeline":
        for it in s.composition.items:
            if not re.search(r"\d", it.year):
                errs.append(f"timeline label '{it.year}' must be a year or date, not a word; use another composition if there are no dates")
    bodies = [t for f, t in fields if f in ("body", "bullets", "detail", "label") and len(t.split()) >= 4]
    if len(bodies) >= 3:
        vague = [t for t in bodies if not _is_concrete(t)]
        if len(vague) * 2 > len(bodies):
            errs.append(f"{len(vague)} of {len(bodies)} statements contain no number, date or name ('{vague[0][:50]}'): replace filler with "
                        "concrete facts from the findings (spare findings are listed in the prompt)")
    shaky = [f for f in cited if f.needs_review]
    if shaky and not any(ATTRIBUTION.search(t) for _, t in fields):
        errs.append(f"findings {[f.id for f in shaky]} rest on one non-official source: attribute them in the text "
                    "('X reportedly ...', 'according to <site>') or leave them out")
    title = s.title.lower()
    body_nums = " ".join(t for f, t in fields if f != "title")
    for w in NUMBER_WORDS:
        if re.search(rf"\b{w}\b", title) and not re.search(rf"\b{w}\b", low):
            errs.append(f"title says '{w}' but the cited findings do not; state only what the cards and findings support")
    for tok in re.findall(r"\d[\d,]*", s.title):
        if tok.replace(",", "") not in body_nums.replace(",", "") and tok.replace(",", "") not in low.replace(",", ""):
            errs.append(f"title number {tok} is not supported by the cards below it or the findings")
    return errs


def claim_errors(s: SlideSpec, findings: dict[str, Finding]) -> list[str]:
    errs: list[str] = quality_errors(s, findings)
    cited = _cited_text(s, findings)
    low = cited.lower()
    fields = visible_fields(s)

    # 1. strong wording the findings do not use
    for field, text in fields:
        for m in OVERCLAIM_RE.finditer(text):
            if not re.search(rf"(?<![-\w]){re.escape(m.group(1).lower())}(?![-\w])", low):
                errs.append(f"'{m.group(1)}' in '{text[:50]}' overstates: the cited findings never say it (use plain wording)")
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
                errs.append(f"number '{tok}' in '{text[:50]}' is not stated in the cited findings (do not compute or round figures)")
    # 3. multi-word proper names in body text must come from the findings
    for field, text in fields:
        if field in HEADINGS:
            continue
        for m in NAME_RE.finditer(text):
            if m.group(0).lower() not in low:
                errs.append(f"name '{m.group(0)}' in '{text[:50]}' does not appear in the cited findings")
    # 4. verbatim copying of source wording
    snippets = [re.findall(r"[a-z0-9']+", findings[r].evidence_snippet.lower()) for r in s.source_refs if r in findings]
    for field, text in fields:
        toks = re.findall(r"[a-z0-9']+", text.lower())
        for sn in snippets:
            grams = {" ".join(sn[i:i + 8]) for i in range(len(sn) - 7)}
            if any(" ".join(toks[i:i + 8]) in grams for i in range(len(toks) - 7)):
                errs.append(f"'{text[:50]}' copies source wording (8+ words); paraphrase it")
                break
    # 5. spelling: unknown words that fewer than two independent sources use are typos until proven otherwise
    seen: set[str] = set()
    for field, text in fields:
        for w in WORD_RE.findall(text):
            if w in seen or w in _KNOWN:
                continue
            seen.add(w)
            if _spell.unknown([w]):
                roots = {_domain_root(f.source_url) for f in findings.values() if w in f"{f.claim} {f.evidence_snippet}".lower()}
                if len(roots) < 2:
                    errs.append(f"'{w}' looks misspelled (not a dictionary word and not used by two independent sources)")
    # 6. numbers and superlatives need independent support: one official source or two independent domains
    slide_nums = any(len(t.rstrip("%").replace(",", "")) >= 2 for _, tx in fields for t in NUM_RE.findall(tx))
    needs_support = bool(s.chart or (s.composition and s.composition.kind == "stats") or slide_nums) and s.layout not in ("TITLE", "SECTION", "SOURCES")
    if needs_support and s.source_refs:
        cs = [findings[r] for r in s.source_refs if r in findings]
        roots = {_domain_root(f.source_url) for f in cs}
        if len(roots) < 2 and not any(f.source_type in TIER_A for f in cs):
            errs.append(f"numbers need two independent sources or one official source; cited only {sorted(roots)}. "
                        "Cite other supporting findings or remove the figures (a vendor alone cannot support claims about itself)")
    return errs
