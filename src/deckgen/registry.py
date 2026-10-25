"""Check registry: every check in the pipeline, its category, and its severity under each gate profile.

category
  correctness         the deck would be wrong or broken without it (contradictions, unsupported facts, overflow, invalid layout)
  model_compensation  exists because the current model needs help (retry counts, strict length rules, item counts, fallbacks);
                      re-evaluate when a stronger model arrives
level
  hard  blocks / triggers repair; the slide is not accepted with it
  warn  recorded in review_before_use.md and the run report; never drops or blocks a slide
Profiles: "strict" (the round-1 behaviour) and "demoted" (heuristics become warnings). Selected by DECKGEN_GATES."""
from __future__ import annotations

from dataclasses import dataclass

from deckgen.config import CONFIG


@dataclass(frozen=True)
class Check:
    id: str
    category: str
    strict: str
    demoted: str
    where: str
    description: str


C, M = "correctness", "model_compensation"
CHECKS: list[Check] = [
    # --- spec validity (spec/validate_spec.py)
    Check("layout_valid", C, "hard", "hard", "spec/validate_spec.py", "layout name exists in the brand manifest"),
    Check("placeholder_valid", C, "hard", "hard", "spec/validate_spec.py", "placeholder names exist on the layout"),
    Check("manifest_max_chars", M, "hard", "warn", "spec/validate_spec.py", "placeholder text within the layout's estimated capacity (true overflow is caught after render)"),
    Check("title_states_message", M, "hard", "warn", "spec/rules.py", "titles state the message (at least 4 words)"),
    Check("icon_valid", C, "hard", "hard", "spec/rules.py", "icons exist in react-icons"),
    Check("layout_needs_content", C, "hard", "hard", "spec/rules.py", "CHART/TABLE/TITLE_ONLY layouts carry their chart, table or composition"),
    # --- provenance and grounding (spec/rules.py, spec/claims.py)
    Check("source_refs_present", C, "hard", "hard", "spec/rules.py", "content slides cite findings, and every cited id exists"),
    Check("stat_year_value_grounded", C, "hard", "hard", "spec/rules.py", "stat values, timeline years and chart values appear in the cited findings"),
    Check("chart_rating_scale", C, "hard", "hard", "spec/rules.py", "no charts of small integer ratings on an invented scale"),
    Check("number_in_text_grounded", C, "hard", "hard", "spec/claims.py", "numbers in body text are stated by the cited findings (no derived figures)"),
    Check("name_in_body_grounded", C, "hard", "hard", "spec/claims.py", "multi-word names in body text appear in the cited findings (headings and labels exempt)"),
    Check("overclaim_word", C, "hard", "hard", "spec/claims.py", "first/only/all/every/... only if the cited findings use the word"),
    Check("title_numbers_supported", C, "hard", "hard", "spec/claims.py", "numbers and number-words in a title are supported by cards and findings"),
    Check("copy_long", C, "hard", "hard", "spec/claims.py", "no verbatim run of copy_hard_words (default 15) from a source snippet"),
    Check("copy_short", M, "hard", "warn", "spec/claims.py", "no verbatim run of copy_warn_words (default 8) from a source snippet"),
    Check("spelling", C, "warn", "warn", "spec/claims.py", "unknown words not used by two independent origins"),
    Check("numbers_need_two_origins_stat", C, "hard", "hard", "spec/claims.py", "stat/chart numbers need two independent origins or one authoritative origin"),
    Check("numbers_need_two_origins_text", C, "hard", "warn", "spec/claims.py", "the same rule for numbers in ordinary text"),
    Check("attribute_review_claims", C, "hard", "warn", "spec/claims.py", "claims resting on one low-tier origin are attributed in the text"),
    Check("faithfulness_unsupported", C, "hard", "hard", "spec/faithfulness.py", "LLM fact-check: no statement unsupported by the cited findings (title included)"),
    Check("faithfulness_vague", M, "hard", "warn", "spec/faithfulness.py", "LLM check: statements that tell the audience nothing concrete"),
    Check("specificity_score", M, "hard", "warn", "spec/claims.py", "share of body statements with a number, date or name above specificity_min"),
    # --- shape and length rules (spec/rules.py)
    Check("composition_capacity", M, "hard", "warn", "spec/rules.py", "per-field character limits derived from renderer geometry"),
    Check("bullet_caps", M, "hard", "warn", "spec/rules.py", "max bullets per list and words per bullet; closing slide caps"),
    Check("chart_table_caps", M, "hard", "warn", "spec/rules.py", "chart category count/length, table cell length"),
    Check("title_length", M, "hard", "hard", "spec/rules.py", "title within two lines at 28 pt (exact counts fed back)"),
    Check("title_period", M, "warn", "warn", "spec/validate_spec.py", "titles do not end with a period"),
    Check("timeline_label_date", M, "hard", "warn", "spec/claims.py", "timeline labels are years or dates"),
    Check("layout_variety", M, "hard", "warn", "spec/rules.py", "no layout/composition three times in a row"),
    Check("speaker_notes_present", M, "hard", "warn", "spec/rules.py", "content slides carry speaker notes"),
    # --- deck level (spec/deck_checks.py, outliner)
    Check("deck_contradiction", C, "hard", "hard", "spec/deck_checks.py", "no two slides state incompatible facts"),
    Check("deck_unclear_reference", M, "hard", "warn", "spec/deck_checks.py", "no reference that needs context the deck never gives"),
    Check("deck_redundancy", M, "hard", "hard", "spec/deck_checks.py", "no two slides make the same point (richer slide kept, other deleted)"),
    Check("outvoted_finding_excluded", C, "hard", "hard", "research/weigh.py", "findings outweighed by a conflicting higher-weight finding never reach slides"),
    Check("outline_distinct_evidence", M, "hard", "hard", "planning/outliner.py", "content slides rest on distinct findings; slide count is a maximum"),
    # --- render-level (qa/)
    Check("render_overflow", C, "hard", "hard", "qa/checks.py + critic", "text fits its box"),
    Check("render_off_slide_margin", C, "hard", "hard", "qa/checks.py", "shapes inside the slide and margins"),
    Check("render_overlap", C, "hard", "hard", "qa/checks.py + critic", "no partial overlaps"),
    Check("render_font_floor", C, "hard", "hard", "qa/checks.py", "no text below the font floor"),
    Check("file_valid", C, "hard", "hard", "qa/validate_pptx.py", "package structure, colors, charts native"),
    Check("content_leftovers", C, "hard", "hard", "qa/slide_text.py", "no leftover placeholder text, every slide has a title"),
    Check("critic_empty_space", M, "warn", "warn", "qa/visual_critic.py", "reported only; never triggers a fix"),
    # --- failure handling and retries (model compensation by definition)
    Check("retry_budget", M, "hard", "hard", "spec/spec_generator.py", "spec_retries / fix_rounds / parse_attempts caps"),
    Check("fallback_slide", M, "hard", "hard", "spec/spec_generator.py", "findings-based fallback slide when repair fails"),
]
BY_ID = {c.id: c for c in CHECKS}


def level(check_id: str, profile: str | None = None) -> str:
    c = BY_ID[check_id]
    return c.demoted if (profile or CONFIG.gate_profile) == "demoted" else c.strict


def table(profile: str | None = None) -> str:
    rows = ["| check | category | strict | demoted | where | what |", "|---|---|---|---|---|---|"]
    rows += [f"| {c.id} | {c.category} | {c.strict} | {c.demoted} | {c.where} | {c.description} |" for c in CHECKS]
    return "\n".join(rows)
