"""Run the live critic on a rendered image dir: python scripts/critic_run.py <images_dir> [out.json]"""
import json, sys
from pathlib import Path
from deckgen.llm import GeminiLLM, Tracer
from deckgen.qa.visual_critic import critique

d = Path(sys.argv[1])
imgs = sorted(d.glob("slide-*.jpg"), key=lambda p: int(p.stem.split("-")[1]))
llm = GeminiLLM(Tracer(d / "critic_trace.jsonl"))
issues = critique(llm, imgs, list(range(1, len(imgs) + 1)), all_images=imgs)
for i in sorted(issues, key=lambda i: (i.slide, i.type)):
    print(f"s{i.slide} {i.severity:6} {i.type:18} {i.description}  -> {i.suggested_fix}")
print(len(issues), "issues;", llm.tracer.summary())
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(json.dumps([i.model_dump() for i in issues], indent=1))
