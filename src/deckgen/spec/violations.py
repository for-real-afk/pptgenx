"""A violation is one failed check: registry id, message for the model/human, and a level from the active gate profile.
hard = the slide is not accepted with it; warn = recorded in the run report and review_before_use.md, never blocks or drops."""
from __future__ import annotations

from dataclasses import dataclass

from deckgen import registry


@dataclass(frozen=True)
class Violation:
    check: str
    message: str
    level: str  # "hard" | "warn"

    @property
    def category(self) -> str:
        return registry.BY_ID[self.check].category

    @property
    def is_hard_correctness(self) -> bool:
        return self.level == "hard" and self.category == "correctness"


# checks whose failure means "the evidence cannot support this" (as opposed to a formatting rule or a model slip)
EVIDENCE_CHECKS = {"source_refs_present", "stat_year_value_grounded", "chart_rating_scale", "number_in_text_grounded",
                   "name_in_body_grounded", "overclaim_word", "title_numbers_supported", "numbers_need_two_origins_stat",
                   "numbers_need_two_origins_text", "faithfulness_unsupported", "copy_long"}


def v(check: str, message: str, profile: str | None = None) -> Violation:
    return Violation(check, message, registry.level(check, profile))


def hard(vs: list[Violation]) -> list[Violation]:
    return [x for x in vs if x.level == "hard"]


def warns(vs: list[Violation]) -> list[Violation]:
    return [x for x in vs if x.level == "warn"]


def classify_failure(remaining_hard: list[Violation], parse_failed: bool = False) -> str:
    """Why a slide could not be written: 'evidence' (findings cannot support it), 'rule' (a formatting/length rule), or 'model' (unparseable output)."""
    if parse_failed and not remaining_hard:
        return "model"
    if any(x.check in EVIDENCE_CHECKS for x in remaining_hard):
        return "evidence"
    return "rule" if remaining_hard else "model"
