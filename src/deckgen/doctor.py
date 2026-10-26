"""Environment health checks behind `deckgen doctor`: render toolchain, Node renderer, fonts, configured models and prices, writable directories.
Each check reports through the injected `echo` and returns whether it passed; the key is only ever tested for presence, never printed."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from deckgen.config import CONFIG
from deckgen.ledger import Prices
from deckgen.providers import get_adapter
from deckgen.qa import soffice

Echo = Callable[[str], None]
REQUIRED_FONTS = ("Carlito", "Caladea", "Liberation")


def _report(echo: Echo, name: str, ok: bool, detail: str = "") -> bool:
    echo(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


def check_node(echo: Echo) -> list[bool]:
    node = shutil.which("node")
    version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip() if node else "not found"
    return [_report(echo, "node", bool(node), version),
            _report(echo, "pptxgenjs installed", (CONFIG.root / "node_modules" / "pptxgenjs").exists(), "run `npm install`")]


def check_render(echo: Echo) -> list[bool]:
    try:
        results = [_report(echo, "render backend (soffice + pdftoppm)", True, soffice.backend())]
        fonts = soffice.list_fonts().lower()
        return results + [_report(echo, f"font {need}", need.lower() in fonts) for need in REQUIRED_FONTS]
    except soffice.RenderError as e:
        return [_report(echo, "render backend", False, str(e))]


def check_models(echo: Echo) -> list[bool]:
    """Fetch each configured provider's live model list and flag a role that points at a model that is not there."""
    results, listed = [], {}
    for role, spec in CONFIG.build_roles().items():
        for provider, model in [(spec.provider, spec.model), *spec.fallbacks]:
            label = f"role {role}: {provider}:{model}"
            try:
                if provider not in listed:
                    listed[provider] = set(get_adapter(provider).list_models())
                present = model in listed[provider]
                results.append(_report(echo, label, present, "" if present else "model not in the provider's model list"))
            except Exception as e:  # noqa: BLE001 - a missing key or no network is a finding, not a crash
                results.append(_report(echo, label, False, f"cannot list models ({type(e).__name__}: {str(e)[:80]})"))
    return results


def check_costs(echo: Echo) -> list[bool]:
    """Anthropic roles: key present and a price for every model (the ledger never guesses a price)."""
    models = sorted({r.model for r in CONFIG.build_roles().values() if r.provider == "anthropic"})
    if not models:
        return []
    missing = Prices.load().missing(models)
    return [_report(echo, "ANTHROPIC_API_KEY set", bool(os.environ.get("ANTHROPIC_API_KEY")), "add it to .env"),
            _report(echo, "prices.yaml complete for " + ", ".join(models), not missing, "; ".join(missing))]


def check_writable(echo: Echo) -> list[bool]:
    results = []
    for directory in (Path(tempfile.gettempdir()), CONFIG.runs_dir):
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".deckgen_probe"
        try:
            probe.write_text("x")
            probe.unlink()
            results.append(_report(echo, f"writable {directory}", True))
        except OSError as e:
            results.append(_report(echo, f"writable {directory}", False, str(e)))
    return results


def run_doctor(echo: Echo = print) -> bool:
    checks = (check_node, check_render, check_models, check_costs, check_writable)
    return all(all(check(echo)) for check in checks)
