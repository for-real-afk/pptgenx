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

## Round 2, step 1: provider layer
- `deckgen/providers/` (base, gemini, groq, anthropic, fake): one file per provider, loaded by name; Groq-specific settings (reasoning flags,
  best-effort json_object, header parsing) live only in groq.py. Anthropic adapter is plain HTTP, untested live (no key).
- `RoleLLM`: stages -> roles (extractor/writer/checker/critic) -> provider:model by `DECKGEN_ROLE_<ROLE>`; checker can sit on another family.
  On-disk cache by prompt hash (`--no-cache`), rate limiter (provider headers + local TPM cap, waits not fails), per-role call/token
  budgets, Pydantic validate-and-retry with the error as feedback, model/role/cached/waited per call in trace.jsonl and per role in run_report.md.
- `registry.py` + `deckgen checks`: every check tagged correctness | model_compensation with strict/demoted severity. Thresholds moved to config.
- `deckgen doctor` lists each configured model against the provider's live model list.
- No GROQ_API_KEY in this environment: Groq adapter is tested with a stubbed HTTP layer only. Live Groq runs wait for the key.

## Round 2, step 2: research depth
- Full pages (no 6,000-char cut), split into ~1,100-char passages, BM25 selects the 4 most relevant per question before extraction (`research/passages.py`).
- Rounds until the raw pool reaches `DECKGEN_TARGET_FINDINGS` (default 100) or a hard budget ends them; budgets raised (160 tool calls, 70 pages,
  1,500 s). 3 queries per question, 6 results each, up to ~8 pages per question; Wikipedia references followed; follow-up rounds reword thin questions.
- `research/weigh.py`: config-driven tiers (`config/source_tiers.json`, per-topic overrides in `config/tier_overrides/`), origin collapse (same publisher
  + MinHash near-copies), independent-origin corroboration (self-sourced vendor pages do not count for their own claims), conflict candidates found
  deterministically (same words, different numbers) and judged by the checker role, resolution by weight (tier, origins, confidence) not count,
  unresolved conflicts recorded with every value and source and excluded from slides. Excluded findings and conflicts appear in review_before_use.md.
- Findings now carry tier, origin_id, independent_origin_count, excluded(+reason); research stats report raw/kept/usable/excluded counts, origins, tier histogram.
- Seeded tests: two copying low-tier pages vs one official finding resolves to the official one; a wrong-year finding is excluded and listed for review.
- Not yet run live (no Groq key; Gemini quota): the 80-120 raw-findings target is untested against real sources.

## Round 2, step 3: hard gates vs warnings
- `spec/violations.py`: every failed check is a Violation (registry id, message, level from the gate profile). `DECKGEN_GATES=strict|demoted`
  (default demoted). rules/claims/validate_spec/faithfulness all emit violations; old string-returning functions remain as wrappers.
- Hard in both profiles: contradictions, ungrounded numbers/names/dates/titles, overclaim words, outvoted findings, invalid layout/placeholders, overflow,
  verbatim copying of 15+ words, stat/chart numbers without two independent origins or one authoritative origin.
- Demoted to warnings: copy 8-14 words, specificity, numbers-in-text two-origin rule, attribution of shaky claims, vague statements, capacity/length rules, variety,
  timeline label, notes. Headings and labels are exempt from the invented-name check. Item minimum is 2.
- Failure sequence: retries with specific feedback -> best attempt kept; if only model_compensation rules still fail the slide is KEPT with warnings (never dropped
  or replaced for a formatting rule) -> chart/table degrade -> findings-based fallback -> drop. Every drop carries a category: evidence | rule | model.
- Run report states the shortfall plainly (requested vs built, drops by category, deleted duplicates, warnings, profile); warnings and drops are in review_before_use.md.

## Round 2, step 4: slide writing as an edit loop
- `spec/spec_generator.py` rebuilt: the writer gets the WHOLE usable pool (best first, one line each) plus the outline's assigned findings in full, writes ONCE and cites ids.
  Checks collect violations; `spec/edit_loop.py` applies small targeted diffs (replace with equal/fewer words, delete an item, cite more findings; no net word growth),
  accepts a candidate only if (hard correctness, hard) does not get worse, keeps the best version, caps rounds (`DECKGEN_FIX_ROUNDS`, default 3).
- Failure sequence per slide: diffs -> keep with warnings if only formatting rules fail -> chart/table degrade -> findings-based fallback -> drop (category evidence|rule|model).
- Redundancy repair keeps the RICHER slide (cited-finding weight + concrete statements) and deletes the other, never title/closing/sources, never below the slide floor (6).
  Contradictions/unclear references are fixed on the later slide with a diff.
- Closing: forward-looking AND sourced (source_refs required); if the findings cannot support a point it writes fewer bullets; last resort is a closing built from validated titles.
- Title slide still built from the request. `generate_slide` is gone; `build_slide`/`write_slide` replace it.

## Round 2, step 5: layout from data shape
- `research/shapes.py` (topic-independent): 3+ findings with the same unit for different named things -> chart hint; 4+ dated findings from one question -> table hint.
  Hints go to the outliner prompt; `attach()` gives each CHART_COMMENTARY/TABLE outline slide its best-matching hint as `data_hint`; the writer prompt shows it
  ("use only these values; cite their ids"). Grounding rules still require every chart/table number to appear in the cited findings and two independent origins.
- Numbers are kept consistent across slides by sourcing both from the same findings plus the existing LLM contradiction pass (no new check type).

## Round 2, step 6: experiment harness
- `deckgen experiment --base-run <run id with brief.json + findings.json> --runs 3 [--strong groq:openai/gpt-oss-120b] [--cheap groq:llama-3.3-70b-versatile]
  [--baseline gemini:...] [--qa]`: variants A (strong + strict), B (cheap + demoted), C (strong + demoted) over the SAME cached findings, fresh outline + spec each run,
  LLM cache off so repeats are real samples. Per-run metrics and `runs/experiments/<id>/summary.md` (slides built mean/per-run/spread, blocking, unsourced,
  specificity, distinct evidence, redundant pairs, fallbacks, warnings, review length, first-attempt pass, calls, tokens, failed runs).
- Gate profile switches per variant at runtime (`registry.use_profile`). Tested offline with scripted fakes. NOT run live: no GROQ_API_KEY here.
- Cost in currency is not computed (no price table); tokens and calls are reported.

## Round 2, step 7: evals (live, Gemini only: no Groq key, no Anthropic key)
- `deckgen eval --runs 3`: run 1 does the research, runs 2-3 reuse its findings and redo outline/spec/QA with the LLM cache off; go/no-go evaluator
  (>=80% of slides on every run, zero blocking, zero unsourced, spread <= 1, <= 1 fallback per deck, review list <= 15) is unit-tested; manual read stays PENDING.
- Live on gemini-3.5-flash-lite (+ flash-lite-latest as checker): WRC 12/14 slides x3 runs, GO; Baghdad Battery (niche, contested) 7/8 x3, GO after three fixes
  that the first live runs exposed: same-origin pairs are not conflicts, conflicts need a shared subject (32 -> 3), title slide box too small, closing body too narrow,
  future-tense claims need support. Five other golden topics NOT run.
- Spread is 0 on both topics but the seed is fixed at 7 and temperature is 0.2: that shows determinism of this setup, not robustness. Different seeds not tried.
