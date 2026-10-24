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

## Next
Step 4 QA: slide_text, validate_pptx, automated shape checks, visual critic, fix loop.
