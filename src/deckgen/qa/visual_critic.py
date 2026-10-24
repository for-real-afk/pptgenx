"""Visual critic. Isolated by design: it gets only slide images and the checklist.
It never sees the spec, the generating code, or what the author intended."""
from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image

from deckgen.llm import LLM
from deckgen.schemas.qa import CriticReport, Issue

BATCH = 5

SYSTEM = (
    "You are a meticulous presentation QA reviewer. You are shown rendered slide images (16:9, 13.33 x 7.5 inches). "
    "Judge only what you can see. Report real, visible defects; do not invent problems and do not praise. "
    "If a slide has no defects, report nothing for it."
)

CHECKLIST = """Inspect EVERY slide against this checklist and report each defect found:
- overflow: text overflowing or cut off at a box or slide edge (check this first)
- overlap: overlapping elements (text through shapes, lines through words, stacked elements)
- footer_collision: source citations or footers colliding with content
- tight_spacing: elements closer than 0.3 in, cards nearly touching, margins under 0.5 in
- uneven_spacing: uneven gaps (empty here, cramped there)
- empty_space: a large area (roughly a fifth of the slide or more) left empty while the rest is filled
- misalignment: columns or similar elements not aligned
- low_contrast: low-contrast text or icons
- excessive_wrap: text boxes too narrow causing excessive wrapping, or a word/number broken across lines (a figure separated from its unit)
- placeholder_text: leftover placeholder content
Severity: high = user-visible breakage (overflow, overlap, unreadable); medium = clearly unpolished; low = minor.
Slides are numbered in the order shown. For each issue give: slide (number), type, severity, description (what and where), suggested_fix (concrete change to content or layout)."""

CONSISTENCY = """These are all slides of one deck, in order. Compare slides of the same kind with each other, not each against itself:
- titles at different heights or sizes, or title alignment differing between content slides (type inconsistent_style)
- chart labels, fonts or colors styled differently across slides (inconsistent_style)
- similar elements (cards, columns, rows) spaced or sized differently between slides (misalignment or uneven_spacing)
- one slide styled heavily while others are plain (inconsistent_style)
Only report differences between slides of the same type; dividers and title slides may differ. Use the same issue format."""


def _prep(images: list[Path], width: int, tmp: Path) -> list[Path]:
    out = []
    for i, p in enumerate(images):
        im = Image.open(p).convert("RGB")
        im = im.resize((width, round(im.height * width / im.width)))
        q = tmp / f"{i:03d}.jpg"
        im.save(q, quality=85)
        out.append(q)
    return out


def critique(llm: LLM, images: list[Path], slide_numbers: list[int], *, all_images: list[Path] | None = None) -> list[Issue]:
    """Pass 1 on `images` (their deck positions in `slide_numbers`); pass 2 consistency on `all_images` if given."""
    issues: list[Issue] = []
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        small = _prep(images, 1280, tmp)
        for k in range(0, len(small), BATCH):
            nums = slide_numbers[k:k + BATCH]
            prompt = (f"{CHECKLIST}\n\nThe {len(nums)} images below are slides {nums} in that order. "
                      f"Use these deck slide numbers in your report.")
            rep = llm.generate(stage="critic", system=SYSTEM, prompt=prompt, schema=CriticReport, images=small[k:k + BATCH], vision=True)
            issues += [i for i in rep.issues if i.slide in nums]
        if all_images and len(all_images) > 1:
            thumbs = _prep(all_images, 800, tmp)
            prompt = f"{CONSISTENCY}\n\nSlide numbers are 1..{len(thumbs)} in the order shown."
            rep = llm.generate(stage="critic_consistency", system=SYSTEM, prompt=prompt, schema=CriticReport, images=thumbs, vision=True)
            issues += [i for i in rep.issues if 1 <= i.slide <= len(thumbs)]
    for i in issues:
        i.source = "critic"
    return issues
