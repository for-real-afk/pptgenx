"""All budgets, model names, paths, thresholds and flags live here (env-overridable)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DEFAULT_MODEL = {"gemini": "gemini-3.5-flash", "groq": "openai/gpt-oss-120b", "anthropic": "claude-sonnet-5-5"}
ROLE_TEMPERATURE = {"extractor": 0.1, "writer": 0.2, "checker": 0.0, "critic": 0.0}  # low and fixed for reproducibility
ROLE_MAX_TOKENS = {"extractor": 2000, "writer": 3000, "checker": 3000, "critic": 1500}  # a runaway response cannot burn the budget


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _budgets() -> dict:
    out = {}
    for r in ("extractor", "writer", "checker", "critic"):
        out[r] = {"calls": _int(f"DECKGEN_BUDGET_{r.upper()}_CALLS", 600), "tokens": _int(f"DECKGEN_BUDGET_{r.upper()}_TOKENS", 3_000_000)}
    return out


def _tpm() -> dict:
    """Optional local tokens-per-minute caps per provider, e.g. DECKGEN_TPM_GROQ=6500 for a free-tier org."""
    return {p: int(os.environ[f"DECKGEN_TPM_{p.upper()}"]) for p in ("groq", "gemini", "anthropic") if os.environ.get(f"DECKGEN_TPM_{p.upper()}")}


@dataclass(frozen=True)
class Config:
    root: Path = ROOT
    runs_dir: Path = ROOT / "runs"
    brands_dir: Path = ROOT / "brand"
    renderer_dir: Path = ROOT / "renderer"
    # LLM roles: DECKGEN_ROLE_<ROLE>="provider:model[,provider:model...]"; unset roles use DECKGEN_LLM_PROVIDER + DECKGEN_MODEL
    llm_provider: str = os.environ.get("DECKGEN_LLM_PROVIDER", "gemini")
    model: str = os.environ.get("DECKGEN_MODEL", "")
    fallback_models: tuple = tuple(m for m in os.environ.get("DECKGEN_FALLBACK_MODELS", "gemini-3.1-flash-lite,gemini-flash-lite-latest,gemini-2.5-flash").split(",") if m)
    vision_model: str = os.environ.get("DECKGEN_VISION_MODEL", "")
    llm_cache: bool = os.environ.get("DECKGEN_NO_CACHE", "") != "1"
    parse_attempts: int = _int("DECKGEN_PARSE_ATTEMPTS", 3)   # validate-and-retry with the validation error as feedback
    net_attempts: int = _int("DECKGEN_NET_ATTEMPTS", 6)       # 429 / 5xx retries per request (waits, does not fail)
    tpm: dict = field(default_factory=_tpm)
    role_budgets: dict = field(default_factory=_budgets)
    # research budgets
    max_tool_calls: int = _int("DECKGEN_MAX_TOOL_CALLS", 160)
    max_pages: int = _int("DECKGEN_MAX_PAGES", 70)
    max_seconds: int = _int("DECKGEN_MAX_SECONDS", 1500)
    max_search_calls: int = _int("DECKGEN_MAX_SEARCH_CALLS", 80)
    target_raw_findings: int = _int("DECKGEN_TARGET_FINDINGS", 100)
    # gate profile: "strict" (every check hard) or "demoted" (heuristics become warnings in review_before_use.md)
    gate_profile: str = os.environ.get("DECKGEN_GATES", "demoted")
    # spec-writing thresholds (model_compensation group: re-evaluate when the stronger model arrives)
    max_bullets: int = _int("DECKGEN_MAX_BULLETS", 5)
    max_words_per_bullet: int = _int("DECKGEN_MAX_WORDS_PER_BULLET", 16)
    spec_retries: int = _int("DECKGEN_SPEC_RETRIES", 3)
    fix_rounds: int = _int("DECKGEN_FIX_ROUNDS", 3)
    copy_hard_words: int = _int("DECKGEN_COPY_HARD_WORDS", 15)
    copy_warn_words: int = _int("DECKGEN_COPY_WARN_WORDS", 8)
    specificity_min: float = _float("DECKGEN_SPECIFICITY_MIN", 0.5)
    slide_floor: int = _int("DECKGEN_SLIDE_FLOOR", 6)
    prompt_aim: float = _float("DECKGEN_PROMPT_AIM", 0.8)   # prompts show limits scaled by this
    pool_prompt_max: int = _int("DECKGEN_POOL_PROMPT_MAX", 100)
    pin_models: bool = os.environ.get("DECKGEN_PIN_MODELS", "") == "1"  # QA/evals: no silent fallback
    faithfulness_check: bool = os.environ.get("DECKGEN_FAITHFULNESS", "1") == "1"
    max_llm_calls: int = _int("DECKGEN_MAX_LLM_CALLS", 1500)
    # cost ledger and budget guard (USD, whole run, all roles, retries and QA included)
    budget_usd: float = _float("DECKGEN_BUDGET_USD", 25.0)
    # writer behaviour (round 3): what worked in the hand-built comparison deck
    writer_source_text: bool = os.environ.get("DECKGEN_WRITER_SOURCE_TEXT", "1") == "1"      # relevant full source passages go to the writer
    writer_chooses_layout: bool = os.environ.get("DECKGEN_WRITER_CHOOSES_LAYOUT", "1") == "1"  # outline layout_hint is a suggestion only
    photos: bool = os.environ.get("DECKGEN_PHOTOS", "1") == "1"                              # real photographs (Wikimedia Commons) on PHOTO_TEXT slides
    whole_deck: bool = os.environ.get("DECKGEN_WHOLE_DECK", "1") == "1"                      # one call writes the whole deck; per-slide writing only repairs hard failures
    hedge_contested: bool = os.environ.get("DECKGEN_HEDGE_CONTESTED", "1") == "1"            # equal-weight conflicts are hedged on the slide, not dropped
    # single-agent loop (v2)
    agent_budget_usd: float = _float("DECKGEN_AGENT_BUDGET_USD", 8.0)      # per run, inside the overall account cap
    agent_max_tool_calls: int = _int("DECKGEN_AGENT_MAX_TOOL_CALLS", 150)
    agent_max_seconds: int = _int("DECKGEN_AGENT_MAX_SECONDS", 2400)
    # QA
    qa_max_rounds: int = 3
    soffice_timeout: int = 120
    render_dpi: int = 150
    min_body_pt: int = 14
    min_caption_pt: int = 10
    min_margin_in: float = 0.5
    soffice_path: str = os.environ.get("SOFFICE_PATH", "")
    docker_image: str = "deckgen-lo"
    # optional module
    corpus_enabled: bool = False
    corpus: dict = field(default_factory=dict)

    def build_roles(self, provider: str | None = None) -> dict:
        """One RoleSpec per role. Resolution: DECKGEN_ROLE_<ROLE> > `provider` argument > DECKGEN_LLM_PROVIDER."""
        from deckgen.llm import ROLES, parse_role

        out = {}
        for r in ROLES:
            kw = {"temperature": ROLE_TEMPERATURE[r], "max_tokens": ROLE_MAX_TOKENS[r], "seed": 7}
            env = os.environ.get(f"DECKGEN_ROLE_{r.upper()}")
            if env and not provider:
                out[r] = parse_role(env, **kw)
                continue
            p = provider or self.llm_provider
            model = (self.vision_model if r == "critic" and self.vision_model else self.model) or DEFAULT_MODEL.get(p, "")
            out[r] = parse_role(f"{p}:{model}", **kw)
            if p == "gemini" and not provider:
                out[r].fallbacks = tuple(("gemini", m) for m in self.fallback_models if m != model)
        return out


CONFIG = Config()
