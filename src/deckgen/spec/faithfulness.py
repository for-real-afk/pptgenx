"""Second opinion on every slide: is each factual statement supported by the cited findings?
Catches plausible-sounding additions that pass the structural rules (ids, numbers, caps)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from deckgen.llm import LLM
from deckgen.schemas import SlideSpec
from deckgen.schemas.research import Finding
from deckgen.spec.violations import Violation, v

SYSTEM = "You are a strict fact-checker. You compare slide text to source findings. Output JSON only."


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unsupported: list[str] = Field(default_factory=list, description="slide statements NOT supported by the findings")
    vague: list[str] = Field(default_factory=list, description="statements that tell the audience nothing concrete")


def slide_text(s: SlideSpec) -> str:
    d = s.model_dump(exclude_none=True)
    for k in ("id", "section", "layout", "source_refs", "source_caption", "speaker_notes"):
        d.pop(k, None)
    return str(d)


def check_slide_text(llm: LLM, s: SlideSpec, cited: list[Finding]) -> list[Violation]:
    facts = "\n".join(f"{f.id}: {f.claim} (evidence: {f.evidence_snippet})" for f in cited)
    prompt = (
        f"FINDINGS:\n{facts}\n\nSLIDE (visible text and data):\n{slide_text(s)}\n\n"
        "List the factual statements on the slide that the findings do not support: invented or altered events, names, "
        "dates, numbers, results, causes and effects, rankings, and vague placeholders presented as data (for example 'N/A', "
        "'Low', 'Extreme'). Treat claim strength as substantive: words such as first, only, all, every, always, never, most, best, exponential, dramatic or unprecedented are unsupported unless the findings use them. Do NOT flag wording, paraphrase, short headings, or light connective phrasing that adds no new "
        "fact. Also treat the TITLE as a claim: it must be supported by the cards beneath it, not stronger than them. Quote each "
        "unsupported statement briefly. Separately list VAGUE statements: cards or bullets that tell the audience nothing they "
        "could not guess from the title (empty praise or filler such as 'a lasting benchmark', 'changed the landscape'). "
        "Return empty lists if everything is supported and concrete."
    )
    v_ = llm.generate(stage="faithfulness", system=SYSTEM, prompt=prompt, schema=Verdict)
    return ([v("faithfulness_unsupported", f"not supported by the cited findings, remove or replace with what the findings say: {b}") for b in v_.unsupported[:6]]
            + [v("faithfulness_vague", f"too vague, replace with a concrete fact (number, name, date, event) from the findings or spare findings: {b}") for b in v_.vague[:4]])


def unsupported_statements(llm: LLM, s: SlideSpec, cited: list[Finding]) -> list[str]:
    """Back-compat name used by tests: the raw feedback strings."""
    return [x.message for x in check_slide_text(llm, s, cited)]
