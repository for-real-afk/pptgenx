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

## Round 3: Anthropic run infrastructure ($25 hard cap)
- `ledger.py`: per-call `runs/<id>/usage.jsonl` (tokens in/out/cache-write/cache-read from the API response, USD from `config/prices.yaml`, latency, local cache hit/miss, retry, outcome),
  stage lines ("stage, calls, tokens, cost, running total of cap"), soft warnings at 60/85%, `usage_summary.md` (also on abort). Budget guard BEFORE every call: counted input (3 chars/token, conservative)
  + max_tokens at output price; abort if total + estimate > cap (`--budget`, default `budget_usd: 25`). Missing price = stop (`PriceMissing`), never guessed.
- `prices.yaml` is deliberately all null: fill input/output/cache_write/cache_read (USD per million tokens) for `claude-haiku-5-5` and `claude-sonnet-5-5` before any run. `deckgen doctor` reports missing prices/key.
- `deckgen estimate`: dry upper/expected estimate per stage; exits 3 (ask first) if upper > $20. Input sizes are fixed assumptions, not tuned.
- Prompt caching: writer prompt split into a stable prefix (style, layouts, limits, rules; `cache_control` on the Anthropic adapter) and a variable part (`spec/writer_prompt.py`).
- Roles: plan/research reading = extractor (Haiku), outline/slides/fixer = writer (Sonnet), checker (Sonnet; same family as writer: known limitation), critic (Haiku). Per-role max_tokens in config.
- Behind default-on flags (`DECKGEN_WRITER_SOURCE_TEXT`, `DECKGEN_WRITER_CHOOSES_LAYOUT`, `DECKGEN_HEDGE_CONTESTED`): verbatim source passages to the writer, layout chosen per slide (hint only),
  equal-weight conflicts kept as `contested` and hedged; every slide's speaker notes end with its sources (+ "Verify:" for contested/single-source). No new check types.
- Tests: cap hit mid-run with a fake provider (never exceeded, summary written), soft warnings, cache-token pricing, prompt split. NOT run live yet.

## Development log: rounds 3 to 8 (what was built, what each live run showed)
Live runs used Anthropic (Haiku 5.5 for reading, Sonnet 5.5 for writing) under a hard USD cap with every call in `usage.jsonl`. Costs below are from the ledger.
- **r3** (staged pipeline, per-slide writer, fact-check as veto): 8 of 14 slides, $1.05. Cause: the checker saw less than the writer and flagged supported text, so 5 of 7 content slides became plain fallbacks.
- **r4**: checker sees the writer's passages and returns a strict issue list that only blocks when code confirms it: 14 of 14, 0 fallbacks, $0.74. Cross-slide contradictions remained.
- **r5/r6** (facts sheet, repair rounds, whole-deck writer): contradiction count did not fall; repair was the most expensive stage and fixed little. A bug meant the whole-deck drafts never reached the slide checks until r8.
- **r7**: repair off by default, one LLM cross-slide check, hedging/layout rules, retry once with more room when cut off: 14 of 14, $0.99, retry waste down from 36% to 4%.
- **r8** (fix pass): passage relevance filter, facts-sheet odd-one-out fix, targeted slide fixes; 0 blocking issues, $1.27 cumulative.
- **Agent loop (v2, `deckgen agent`)**: one Sonnet agent with search / fetch_page / read_source / summarize_sources / files / validate_spec / build_deck / render_slides / finish, a skill file (`skills/deck_agent/SKILL.md`) and a budget guard before every call. a1 crashed on a replayed thinking block; a2 never viewed a slide (render bug, 0 views); a3 viewed all 12 slides and was stopped by the $8 budget guard at $7.71 with 12 of 14 slides built.
- Known gaps: no photos live (needs `DECKGEN_CONTACT`; Wikimedia returned 403 without a contact), PowerPoint opening not verified, cross-slide checker output is noisy, checker is the same model family as the writer.
- Cleanup: `cli.py` is options-and-printing only; orchestration is in `workflows.py`, health checks in `doctor.py`; ruff config in `pyproject.toml`; env files are untracked (`.env.example` and `config/anthropic_run.env.example` document the variables).

## Agent v3 (focused research, code-written deck, room to iterate): built offline, NOT yet run live
- **Research** (`agent/sources.py`): top-tier ladder; the first 5 pages are free, later ones (hard cap 12) need a named `GAP:` line in `notes.md` quoted in the `fetch_page` call (2 fetches per gap); reference links to higher-tier hosts are returned from each fetched page; Haiku `summarize_sources` is for breadth only. One conflict rule lives in the skill (higher tier wins; ties say "sources differ" once); no facts-sheet stage.
- **Deck as code** (`renderer/run_script.js`, `renderer/helpers.js`, `agent/builder.py`): the agent writes a pptxgenjs script that composes slides on the brand frame; the runner uses a vm context with no process/network, a read-only `fs` limited to `work/`, a time limit and a stripped environment, then writes the deck and applies the theme; the existing file validation and geometry checks follow. Errors come back with the script line. `finish` refuses on an invalid file, any overflow or off-slide issue, an unviewed slide, or missing review notes.
- **Budget** (`agent/phases.py`): default $12; research capped at 25%, writing 35%, look-and-fix gets the rest, 10% reserve for wrap-up turns; a capped phase hands over (research tools close); no render-and-fix round starts when the money above the reserve is less than one estimated round. Context tokens per turn are logged in `usage.jsonl`.
- **Account cap**: `deckgen agent` sums every run's `usage.jsonl` and stops (asks) when the run budget exceeds what is left under the $25 account cap.
- Deviation to note: titles on content slides are 28 pt (set by the brand layouts, which the old pipeline shares), not 36 pt.
- Offline tests: gap rule, phase hand-over, wrap-up, budget abort, sandbox (no outside reads, no network, error line numbers), overflow reported with its slide, finish refusals, view caps. A real Docker render of a script-built deck was checked by hand.

### Agent v3 first live run (`wrc-agent-v3`, $5 budget, 85 tool-call limit): $2.22, 13 slides
- Phases: research $0.98 (24 turns, 5 pages, 1 gap named), write $0.54 (11 turns), look $0.70 (34 turns). No phase hand-over fired; the 85-tool-call limit ended the run, not the money.
- What went wrong: about 10 turns were spent probing the helper API (printing helper source onto slides, 4 builds failing with `undefined.length`); 19 builds and 19 whole-script rewrites; the run hit the tool limit before review notes and the look-at-every-slide pass finished.
- Bug found and fixed: salvage picked a one-slide debug probe as "best build" (it had fewer issues than the real deck). Ranking is now: fewest hard issues, then most slides, then fewest blocking issues. The deliverable was re-selected from the run's own builds (build 19), no new model spend.
- Fixes: tool-limit wrap-up at 85% of the limit, helper arguments validated with clear messages and exact signatures in the skill, `edit_build_script` for small fixes.

### Agent v3 fix pass (`wrc-agent-v3-fix`, $0.60 cap): $0.12, 6 model turns
- The build runner now removes slide placeholders that hold no text (`renderer/prune.js`), so decks have no empty placeholders; verified on the rebuilt deck at no model cost (build 19 had 3, the rebuild has 0, text of all 13 slides identical).
- `deckgen agent-fix` runs a bounded pass on a finished agent run: starts in the look phase, research tools closed, 8,000 output tokens per turn, only changed slides must be looked at again. Tool-call limit default is now 120 (wrap-up at 85% unchanged).
- Slides changed: 3, 10, 11, 12, 13. Slides 1, 2, 4 to 9 were not touched and not re-checked by the model. Account spend after the pass: $22.25 of $25.

### Agent polish pass (`wrc-agent-v3-polish`, $0.80 cap): $0.57, 18 model turns, 40 tool calls
- Helpers: `h.cards/stats/timeline/comparison/icon_rows` are now content-fitted (`renderer/compose/fit.js`: card height from text, body >= 16 pt, headings >= 20 pt, stat numbers 60 pt, centred block); new `h.title` (36/32 pt), `h.chartSlide` (chart ~65% width, 16 pt commentary, 12 pt caption), `h.closing` (24 pt, centred), `h.sources` (two columns, 16 pt, title + URL + tier); icons take real `alt` text, items can use `badge: n` instead of an icon.
- Agent-fix mode now decides which slides must be looked at again by a per-slide fingerprint (shape names, positions, sizes, text, font size), so re-laid slides count as changed.
- Fixed after the run, at no model cost: the sources slide overlapped the title box (columns now start below it), the chart caption is allowed under the 14 pt floor (like the source caption), the closing bullets use a centred text box (the layout placeholder ignored vertical alignment). Deck re-selected from a rebuild of the agent's final script; text of all 13 slides identical.
