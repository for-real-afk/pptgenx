from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import typer

from deckgen.config import CONFIG

app = typer.Typer(no_args_is_help=True, add_completion=False)
brand_app = typer.Typer(no_args_is_help=True)
app.add_typer(brand_app, name="brand")


def _check(name: str, ok: bool, detail: str = "") -> bool:
    typer.echo(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    return ok


@app.command()
def doctor() -> None:
    """Check render toolchain, Node renderer deps, fonts, writable dirs."""
    from deckgen.qa import soffice

    results = []
    node = shutil.which("node")
    results.append(_check("node", bool(node), subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip() if node else "not found"))
    results.append(_check("pptxgenjs installed", (CONFIG.root / "node_modules" / "pptxgenjs").exists(), "run `npm install`"))
    try:
        mode = soffice.backend()
        results.append(_check("render backend (soffice + pdftoppm)", True, mode))
        fonts = soffice.list_fonts()
        for need in ("Carlito", "Caladea", "Liberation"):
            results.append(_check(f"font {need}", need.lower() in fonts.lower()))
    except soffice.RenderError as e:
        results.append(_check("render backend", False, str(e)))
    for d in (Path(tempfile.gettempdir()), CONFIG.runs_dir):
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".deckgen_probe"
        try:
            probe.write_text("x"); probe.unlink(); results.append(_check(f"writable {d}", True))
        except OSError as e:
            results.append(_check(f"writable {d}", False, str(e)))
    raise typer.Exit(0 if all(results) else 1)


@brand_app.command("build")
def brand_build(brand: Path = typer.Option(..., help="brand dir, e.g. brand/default_v1")) -> None:
    """layouts.js -> manifest.json."""
    r = subprocess.run(["node", str(CONFIG.renderer_dir / "gen_manifest.js"), str(brand.resolve())], capture_output=True, text=True)
    typer.echo(r.stdout + r.stderr)
    raise typer.Exit(r.returncode)


if __name__ == "__main__":
    app()
