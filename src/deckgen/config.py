"""All budgets, model names, paths, thresholds and flags live here (env-overridable)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Config:
    root: Path = ROOT
    runs_dir: Path = ROOT / "runs"
    brands_dir: Path = ROOT / "brand"
    renderer_dir: Path = ROOT / "renderer"
    # LLM
    llm_provider: str = os.environ.get("DECKGEN_LLM_PROVIDER", "gemini")
    model: str = os.environ.get("DECKGEN_MODEL", "")
    fallback_models: tuple = tuple(m for m in os.environ.get("DECKGEN_FALLBACK_MODELS", "gemini-3.1-flash-lite,gemini-flash-lite-latest,gemini-2.5-flash").split(",") if m)
    vision_model: str = os.environ.get("DECKGEN_VISION_MODEL", "")
    # research budgets
    max_tool_calls: int = _int("DECKGEN_MAX_TOOL_CALLS", 45)
    max_pages: int = _int("DECKGEN_MAX_PAGES", 25)
    max_seconds: int = _int("DECKGEN_MAX_SECONDS", 300)
    # spec limits (enforced at writing time)
    max_bullets: int = 5
    max_words_per_bullet: int = 16
    spec_retries: int = 3
    pin_models: bool = os.environ.get("DECKGEN_PIN_MODELS", "") == "1"  # QA/evals: no silent fallback
    faithfulness_check: bool = os.environ.get("DECKGEN_FAITHFULNESS", "1") == "1"
    max_llm_calls: int = _int("DECKGEN_MAX_LLM_CALLS", 150)
    max_search_calls: int = _int("DECKGEN_MAX_SEARCH_CALLS", 60)
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


CONFIG = Config()
