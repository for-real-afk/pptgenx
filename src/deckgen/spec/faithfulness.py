"""Second opinion on every slide: is each factual statement supported by the cited findings?
Catches plausible-sounding additions that pass the structural rules (ids, numbers, caps)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from deckgen.llm import LLM
from deckgen.schemas import SlideSpec
from deckgen.schemas.research import Finding

SYSTEM = "You are a strict fact-checker. You compare slide text to source findings. Output JSON only."


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unsupported: list[str] = Field(default_factory=list, description="slide statements NOT supported by the findings")


def slide_text(s: SlideSpec) -> str:
    d = s.model_dump(exclude_none=True)
    for k in ("id", "section", "layout", "source_refs", "source_caption", "speaker_notes"):
        d.pop(k, None)
    return str(d)


def unsupported_statements(llm: LLM, s: SlideSpec, cited: list[Finding]) -> list[str]:
    facts = "\n".join(f"{f.id}: {f.claim} (evidence: {f.evidence_snippet})" for f in cited)
    prompt = (
        f"FINDINGS:\n{facts}\n\nSLIDE (visible text and data):\n{slide_text(s)}\n\n"
        "List every factual statement on the slide (events, names, dates, numbers, causes, rankings, results) that the "
        "findings do not clearly support, including vague placeholders presented as data (for example 'N/A', 'Low', 'Extreme') "
        "and explanations or causes the findings never state. Quote each unsupported statement briefly. Return an empty "
        "list if everything is supported."
    )
    return llm.generate(stage="faithfulness", system=SYSTEM, prompt=prompt, schema=Verdict).unsupported
