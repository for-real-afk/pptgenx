# PROGRESS

## Done (Phase 1, steps 1-3)
- Scaffold, config, `deckgen doctor`, `deckgen brand build`.
- `qa/soffice.py`: only LibreOffice caller. Local soffice if present, else Docker image `deckgen-lo` (`Dockerfile.lo`).
- Brand `default_v1`: `theme.json`, `layouts.js` (7 layouts), generated `manifest.json`.
- Renderer: `build_deck.js`, `apply_theme.js`, compose (cards, stats, timeline, comparison, icon_rows), native chart, table, notes, sections.
- Strict `DeckSpec` schema + `validate_spec` (layout enum, placeholder names, max_chars).
- 7 tests pass (grid math, spec validation, misspelt layout rejected, apply_theme throws on scheme color in hex option, deck opens, render = 1 image per slide).
- Hand-written 10-slide fixture renders cleanly in Docker LibreOffice.

## Decisions / deviations (logged per constraints)
- Windows host, no soffice/poppler: render via Docker image (user choice).
- LLM provider: user supplied a Gemini key, not Anthropic. LLM client will sit behind an interface; `DECKGEN_LLM_PROVIDER=gemini` default. Keys live in `.env` (gitignored).
- Search provider: Tavily.
- `image_request` / image+text compose deferred (no image source in POC).
- python-pptx `shapes.title` returns None for pptxgenjs decks (placeholder idx != 0); slide_text must locate titles by placeholder type.
- Icon colors are baked into PNGs (hex from theme), so they do not follow later theme edits.
- Timeline/comparison leave empty space at the bottom of the slide; visual critic should flag, tune in step 4.

## Step 4 (QA) - done
- slide_text, validate_pptx (baselines two known pptxgenjs quirks), checks, visual critic, fix loop. Critic runs on images + checklist only.
- Tuning round after review: never-worse loop (revert + stop if score does not improve), phase order overflow > overlap > contrast,
  trim/delete-only diffs with enforced no-word-growth, blocking policy in `qa/policy.py`, critic no longer asked to measure,
  design context given to critic, model pinning + models logged.
- Fixture acceptance (pinned gemini-3.1-flash-lite): 0 blocking issues, 3 non-blocking notes listed.

## Steps 5-7 - built
- Flat `SlideDraft` -> `SlideSpec` conversion (tested); `spec/capacity.py` derives text limits from renderer geometry
  (tested at max capacity: found and fixed a 5-row icon circle overflow); `spec/rules.py` (capacity, provenance,
  number grounding, no invented rating scales, variety); per-slide retries with concrete feedback; chart/table slides
  degrade to text layouts when the findings cannot ground them.
- Research: LangGraph loop, Tavily + httpx behind interfaces, disk cache in `runs/_cache`, hard per-run caps,
  sanitizer + quoted data, deny-list for forums/essay mills, meta-findings filter, cross-check recording conflicts.
- Planner, arcs, outliner with approval checkpoint, pipeline with resumable stages, `generate`, `eval`, README.

## Decisions / gotchas
- Gemini free tier: 20 req/day per model on some models; use `DECKGEN_MODEL=gemini-3.1-flash-lite`.
- Writing new files: GateGuard denies the first Write per file; retry works. Long bash heredocs with odd quotes fail.
- Not done: real PowerPoint open check (user), images, Phase 2.

## Round after external review of the WRC deck
- Title failure: titles now two lines at 28 pt (hard limit 106 chars, aim 53), exact "remove at least N chars" feedback, prompts aim at 80% of limits. Printing press now completes.
- Seeded-claim tests: 8 deterministic seeds (number, year, name, "first", "exponential", derived "4%", "all", typo) all flagged; live LLM fact-checker flags 5/5 seeds and passes the good slide. Writing the seed test found a real gap (sentence-initial names) - fixed.
- New checks: overclaim words, numbers/names must appear in cited findings, verbatim-copy, spelling, vendor-only/one-source numbers, cross-slide consistency (LLM), redundancy, question coverage with drop reasons. Repair pass regenerates the later slide of a problem; failures are non-fatal.
- Grounded fallback slide (findings' own claims) when 4 attempts fail; counted in the tracer (`spec_slide_fallback`).
- Fix loop on generated WRC deck with 2 injected overflows: 6 blocking -> 0 in one round, never worse.
- Not re-run after the new checks: Apollo, mRNA, remote work (Gemini quota). Not done: follow-up research for dropped questions, corroboration of strong claims at extraction time, following Wikipedia citations.

## Round after second external review (research depth, specificity, redundancy)
Built: follow-up searches for thin questions, Wikipedia reference following, corroboration + needs_review, evidence-bounded outline
(+repair), specificity/attribution/title checks, spare findings in prompts, redundancy deletion, title slide from request, review-before-use
list, quality metrics, grounded fallbacks, outvoted-findings exclusion, 2-item compositions.
Live results on WRC with gemini-3.5-flash-lite (same topic, different runs): 14 slides (before checks, with flaws) -> 5 -> 8 -> 6 slides.
Finding: the rule stack is stricter than a flash-lite model can satisfy on thin research; correctness went up, completeness collapsed, and
run-to-run variance is large (v4 vs v5 on IDENTICAL research gave 8 vs 6 slides). Decks now say loudly when they are short.
Open decisions: stronger model for spec/fact-check; which gates become warnings in the review list instead of hard fails (10-word copy rule,
heading-name check, two-source numbers); fetch full pages for research (6000-char cap, ~8 pages per run).
