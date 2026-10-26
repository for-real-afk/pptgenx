"""Second opinion on every slide: is each factual statement supported by the cited findings and the source passages the writer saw?
The checker returns a strict list of issues (empty = pass). An issue blocks the slide only when code confirms it: the quote is verbatim on the slide
and its key numbers/names are absent from everything the writer was given. Otherwise it is a warning on the review list."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from deckgen.llm import LLM
from deckgen.schemas import SlideSpec
from deckgen.schemas.research import Finding
from deckgen.spec.claims import NUM_RE, name_core
from deckgen.spec.violations import Violation, v

NL = chr(10)
SYSTEM = "You are a strict fact-checker. You compare slide text to source findings and passages. Output JSON only."
NAME_TOKEN = re.compile(r"\b[A-Z][\w'-]{2,}(?:\s+[A-Z][\w'-]{2,})*")


class Issue(BaseModel):
    model_config = ConfigDict(extra="ignore")
    claim_quote: str = Field(description="the exact words from the slide that are not supported")
    kind: Literal["unsupported", "contradicted", "vague"]
    contradicting_or_missing_passage: str = Field(default="", description="the passage that contradicts it, or what is missing")


class Verdict(BaseModel):
    model_config = ConfigDict(extra="ignore")
    issues: list[Issue] = Field(default_factory=list, description="EMPTY when the slide is fully supported; only real problems go here")


def slide_text(s: SlideSpec) -> str:
    d = s.model_dump(exclude_none=True)
    for k in ("id", "section", "layout", "source_refs", "source_caption", "speaker_notes", "photo"):
        d.pop(k, None)
    return str(d)


def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^\w%.,'-]+", " ", t.lower()).split())


def key_tokens(quote: str) -> list[str]:
    """Numbers and proper names in a quote: what must be traceable for a claim to count as supported."""
    nums = [n.replace(",", "").rstrip(".") for n in NUM_RE.findall(quote)]
    names = [name_core(m) for m in NAME_TOKEN.findall(quote)]
    return [t for t in nums + names if t]


def confirmed(quote: str, slide_txt: str, evidence: str) -> bool:
    """A hard failure needs both: the quote is verbatim on the slide, and one of its key numbers/names appears in NO passage or finding the
    writer was given (case-insensitive). A quote with no key token cannot be confirmed by code, so it stays a warning."""
    if not quote.strip() or _norm(quote) not in _norm(slide_txt):
        return False
    ev = _norm(evidence).replace(",", "")
    keys = key_tokens(quote)
    return bool(keys) and any(_norm(k).replace(",", "") not in ev for k in keys)


def check_slide_text(llm: LLM, s: SlideSpec, cited: list[Finding], source_text: str = "") -> list[Violation]:
    facts = NL.join(f"{f.id}: {f.claim} (evidence: {f.evidence_snippet})" for f in cited)
    prompt = (
        f"FINDINGS:{NL}{facts}{NL}{NL}"
        + (f"SOURCE PASSAGES (the writer also saw these; a statement they support counts as supported):{NL}{source_text}{NL}{NL}" if source_text else "")
        + f"SLIDE (visible text and data):{NL}{slide_text(s)}{NL}{NL}"
        "Report only statements on the slide that the findings and passages do NOT support or that they contradict: invented or altered events, names, "
        "dates, numbers, results, causes and effects, rankings. Do NOT report wording, paraphrase, short headings, light connective phrasing, or "
        "anything that IS supported. Claim strength matters: first, only, all, every, never, most, best are unsupported unless the sources use them. "
        "The TITLE is a claim too. Return `issues` as a list; each issue has claim_quote (the exact slide words), kind (unsupported | contradicted | "
        "vague) and contradicting_or_missing_passage. kind vague = a statement that tells the audience nothing concrete. "
        "If everything is supported, return an EMPTY list. Never put notes such as 'supported' or 'no issue' in the list."
    )
    try:
        verdict = llm.generate(stage="faithfulness", system=SYSTEM, prompt=prompt, schema=Verdict)
    except ValueError as e:  # unparseable after the retries: the slide is kept and the lapse is logged as a warning
        return [Violation("faithfulness_unsupported", f"fact-check output could not be parsed ({str(e)[:100]}); slide kept unchecked", "warn")]
    evidence = facts + " " + source_text
    visible, out = slide_text(s), []
    for i in verdict.issues[:8]:
        if i.kind == "vague":
            out.append(v("faithfulness_vague", f"too vague, replace with a concrete fact from the findings: {i.claim_quote}"))
            continue
        msg = f"'{i.claim_quote}' ({i.kind}): {i.contradicting_or_missing_passage[:200]}"
        if confirmed(i.claim_quote, visible, evidence):
            out.append(v("faithfulness_unsupported", "not supported by the findings or passages, remove or replace with what they say: " + msg))
        else:
            out.append(Violation("faithfulness_unsupported", "possible unsupported statement (not confirmed by the key-token check): " + msg, "warn"))
    return out


def unsupported_statements(llm: LLM, s: SlideSpec, cited: list[Finding], source_text: str = "") -> list[str]:
    """Back-compat name used by tests: the raw feedback strings."""
    return [x.message for x in check_slide_text(llm, s, cited, source_text)]
