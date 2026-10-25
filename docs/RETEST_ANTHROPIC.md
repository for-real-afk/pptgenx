# Re-test list for when the Anthropic key arrives

Nothing in the pipeline needs code changes: set `ANTHROPIC_API_KEY` in `.env`, point roles at the new model in config, and re-run the same experiments.

## 1. Switch on
1. `.env`: `ANTHROPIC_API_KEY=...` (the adapter is `src/deckgen/providers/anthropic.py`, plain HTTP; it has only been tested against a stubbed HTTP layer).
2. `deckgen doctor`: each role's model is checked against `GET /v1/models`; fix any id the doctor reports as missing.
3. Roles, for example: `DECKGEN_ROLE_WRITER=anthropic:<model>`, `DECKGEN_ROLE_EXTRACTOR=anthropic:<cheaper model>`, `DECKGEN_ROLE_CHECKER=<a different family than the writer>`, `DECKGEN_ROLE_CRITIC=anthropic:<vision-capable model>`.
4. Adapter smoke test (one call per role): run `deckgen generate --topic "..." --slides 6 --auto-approve --no-cache` and read `trace.jsonl`: the model, tokens in/out and rate-limit headers must appear per call.

## 2. Re-run, in this order
1. `deckgen experiment --base-run <run with cached findings> --runs 3 --strong anthropic:<model> --cheap anthropic:<cheaper model> --baseline groq:openai/gpt-oss-120b`
   Compare slide count, spread, fallback slides, warnings, first-attempt pass, tokens. This decides the writer.
2. `deckgen eval --runs 3` on all 7 golden topics with one pinned model per role (`DECKGEN_PIN_MODELS=1`). Compare against `evals/baseline.json` and the Groq/Gemini numbers.
3. Manual read of two generated decks and their `review_before_use.md` (the human part of the go/no-go).

## 3. Re-evaluate the `model_compensation` checks (see docs/CHECKS.md, `deckgen checks`)
With a stronger writer these may no longer pay for themselves. Each is a threshold in `config.py` or a level in `registry.py`:

| what | where | try |
|---|---|---|
| retries and fix rounds | `DECKGEN_SPEC_RETRIES`, `DECKGEN_FIX_ROUNDS`, `DECKGEN_PARSE_ATTEMPTS` | lower to 1-2 and see whether first-attempt pass stays high |
| prompt aim ratio for length limits | `DECKGEN_PROMPT_AIM` (0.8) | raise towards 1.0 |
| per-field capacity caps and bullet caps | checks `composition_capacity`, `bullet_caps`, `chart_table_caps` | keep as warnings; render QA still catches real overflow |
| verbatim-copy warning window | `DECKGEN_COPY_WARN_WORDS` (8) | probably unnecessary |
| specificity threshold | `DECKGEN_SPECIFICITY_MIN` (0.5) and `faithfulness_vague` | check whether the vague-statement flag still fires |
| item-count minimum, layout variety, notes required | schema min 2, `layout_variety`, `speaker_notes_present` | relax or drop |
| findings-based fallback slide and closing-from-titles | `fallback_slide` | count how often it fires; if never, remove it |
| two-line titles and title length | `title_length` | the stronger model may hit one line by itself |
| slide floor for redundancy deletion | `DECKGEN_SLIDE_FLOOR` (6) | judge by deck quality |
| research budgets | `DECKGEN_MAX_PAGES`, `DECKGEN_TARGET_FINDINGS` | a stronger extractor may need fewer pages for the same pool |

## 4. Keep regardless of model (correctness checks)
Contradiction pass, ungrounded numbers/names/dates, overclaim words, title-number support, 15-word verbatim copy, outvoted-finding exclusion, source tiers and independent-origin weighing, invalid layout, overflow and file validation.

## 5. Things only a real run can tell us
- Whether the Anthropic adapter handles the vision critic's image batches (size limits) and rate-limit headers as assumed.
- Prompt caching: the repeated system prompt and rules are already a stable prefix; check cache-hit fields once the adapter is live (not yet read by the adapter).
- Structured output: the adapter currently relies on prompt-plus-Pydantic validation only; try provider-side schemas as an optimisation, never as the correctness mechanism.
