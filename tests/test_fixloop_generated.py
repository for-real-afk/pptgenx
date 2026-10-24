"""Fix loop on a GENERATED deck with injected defects (live: needs the render backend and a Gemini key).
Requirement: the loop never returns a deck worse than the one it was given, and it should clear the defects."""
import os
from pathlib import Path

import pytest

from deckgen.llm import GeminiLLM, Tracer
from deckgen.qa import fix_loop, policy, soffice
from deckgen.schemas import DeckSpec

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "runs" / "eval-the-golden-era-of-wrc-group-b-1980s" / "deck_spec.final.json"
BRAND = ROOT / "brand" / "default_v1"
LIVE = pytest.mark.skipif(not (os.environ.get("DECKGEN_LIVE") and os.environ.get("GEMINI_API_KEY") and GENERATED.exists()),
                          reason="needs DECKGEN_LIVE, GEMINI_API_KEY and a generated deck")
FLUFF = " This sentence adds more background detail that nobody asked for and that does not fit the box at all."


def inject(spec: DeckSpec) -> tuple[DeckSpec, list[int]]:
    """Inflate one body text on each of two slides: a first card/row body, or the first body bullet."""
    slides, hit = list(spec.slides), []
    for i, s in enumerate(slides):
        if len(hit) == 2 or s.layout in ("TITLE", "SOURCES", "CLOSING"):
            continue
        d = s.model_dump(exclude_none=True)
        if s.composition and s.composition.kind in ("cards", "icon_rows"):
            d["composition"]["items"][0]["body"] += FLUFF * 3
        elif isinstance(d.get("placeholders", {}).get("body"), list):
            d["placeholders"]["body"][0] += FLUFF * 3
        else:
            continue
        slides[i] = type(s).model_validate(d)
        hit.append(i + 1)
    return spec.model_copy(update={"slides": slides}), hit


@LIVE
def test_loop_clears_injected_overflow_and_is_never_worse(tmp_path):
    try:
        soffice.backend()
    except soffice.RenderError:
        pytest.skip("no render backend")
    broken, hit = inject(DeckSpec.model_validate_json(GENERATED.read_text(encoding="utf-8")))
    assert len(hit) == 2
    llm = GeminiLLM(Tracer(tmp_path / "t.jsonl"))
    best, rep = fix_loop.run_qa(broken, BRAND, tmp_path, llm, fix_loop.LLMFixer(llm))
    start = rep.rounds[0].score
    assert start > (0, 0), "defects were not detected at all"
    scores = [r.score for r in rep.rounds if r.accepted]
    assert all(b <= a for a, b in zip(scores, scores[1:])), f"score rose between accepted rounds: {scores}"
    assert policy.score(rep.remaining) <= start
    print(fix_loop.report_markdown(rep))
    assert policy.blocking(rep.remaining) == [], f"defects remain: {[(i.slide, i.type) for i in policy.blocking(rep.remaining)]}"
