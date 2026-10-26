"""Phase budget for the agent: research -> write -> look (render-and-fix) -> wrapup.

Research and writing each have a hard cap (a fraction of the run budget). A phase that reaches its cap ends and hands over to the next one;
it does not keep spending. Look-and-fix gets everything that is left except a reserve, which is only used for the wrap-up turns (review notes, finish).
A render-and-fix round is never started when the money left (above the reserve) is less than the estimated cost of one round."""
from __future__ import annotations

from dataclasses import dataclass, field

from deckgen.ledger import Ledger

PHASES = ("research", "write", "look", "wrapup")
TURNS_PER_ROUND = 3          # build + render + edit: what one render-and-fix round costs, in model turns
WRAPUP_TURNS = 6


@dataclass
class PhaseBudget:
    total: float
    research_frac: float
    write_frac: float
    look_min_frac: float
    reserve_frac: float
    phase: str = "research"
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.research_frac + self.write_frac + self.look_min_frac + self.reserve_frac > 1.0 + 1e-9:
            raise ValueError("phase fractions (research + write + look minimum + reserve) must add up to at most 1")

    def cap(self, phase: str) -> float:
        return {"research": self.research_frac, "write": self.write_frac}.get(phase, 1.0) * self.total

    @property
    def usable(self) -> float:
        """What turns may spend: the budget minus the reserve."""
        return self.total * (1 - self.reserve_frac)

    def advance(self, to: str, why: str = "") -> bool:
        """Move forward only (a finished phase never reopens). Returns True if the phase changed."""
        if PHASES.index(to) <= PHASES.index(self.phase):
            return False
        self.phase = to
        if why:
            self.notes.append(why)
        return True

    def round_cost(self, ledger: Ledger) -> float:
        """Estimated cost of one render-and-fix round: the average look-phase turn so far (else the last model turns) times TURNS_PER_ROUND."""
        turns = [r["cost_usd"] for r in ledger.rows if r["pipeline_stage"] == "look" and r["role"] != "tool"]
        if not turns:
            turns = [r["cost_usd"] for r in ledger.rows if r["role"] != "tool"][-3:]
        return (sum(turns) / len(turns) if turns else 0.0) * TURNS_PER_ROUND

    def review(self, ledger: Ledger) -> list[str]:
        """Check the phase against its money. Moves the phase on when a cap is reached and returns messages for the agent (one per change)."""
        msgs: list[str] = []
        if self.phase == "research" and ledger.spent("research") >= self.cap("research"):
            self.advance("write", "research budget used")
            msgs.append("HANDOVER: the research budget is used up. Search, fetch and summarize are closed. Put what you have into notes.md and facts.md "
                        "and write the build script now. Anything you could not settle goes into review.md.")
        if self.phase == "write" and ledger.spent("write") >= self.cap("write"):
            self.advance("look", "writing budget used")
            msgs.append("HANDOVER: the writing budget is used up. Build what you have now with run_build, then look at the slides and fix them.")
        if self.phase == "look" and ledger.total + self.round_cost(ledger) > self.usable:
            self.advance("wrapup", "no budget for another render-and-fix round")
            msgs.append("WRAP-UP: there is no budget left for another render-and-fix round. Do not build or render again. Write review.md "
                        "(including every defect you saw but did not fix) and call finish now.")
        return msgs
