"""The only place that calls LibreOffice / pdftoppm.

Uses a local soffice when available (SOFFICE_PATH or PATH), else the `deckgen-lo` Docker image.
One -env:UserInstallation profile per worker: profiles are locked, sharing one hangs.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from deckgen.config import CONFIG


class RenderError(RuntimeError):
    pass


def _local_soffice() -> str | None:
    cands = [CONFIG.soffice_path, shutil.which("soffice"), shutil.which("libreoffice"),
             r"C:\Program Files\LibreOffice\program\soffice.exe"]
    return next((c for c in cands if c and Path(c).exists()), None)


def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "image", "inspect", CONFIG.docker_image], capture_output=True)
    return r.returncode == 0


def backend() -> str:
    if _local_soffice() and shutil.which("pdftoppm"):
        return "local"
    if _docker_ready():
        return "docker"
    raise RenderError("no render backend: install soffice+pdftoppm or build the Docker image (deckgen doctor)")


def _run(cmd: list[str], timeout: int, trace: Path | None) -> subprocess.CompletedProcess:
    """Run with timeout; kill the whole process tree on timeout (Windows: taskkill /T)."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
        out, err = proc.communicate()
        raise RenderError(f"timeout after {timeout}s: {' '.join(cmd[:4])}") from None
    finally:
        if trace:
            trace.parent.mkdir(parents=True, exist_ok=True)
            with trace.open("a", encoding="utf-8") as f:
                f.write(f"$ {' '.join(cmd)}\n{locals().get('out', '')}\n{locals().get('err', '')}\n")
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _soffice_args(profile: Path, outdir: str, src: str) -> list[str]:
    return ["--headless", "--invisible", "--norestore", "--nologo", "--nodefault",
            f"-env:UserInstallation={profile.as_uri() if isinstance(profile, Path) else profile}",
            "--convert-to", "pdf", "--outdir", outdir, src]


def pptx_to_pdf(pptx: Path, out_dir: Path, worker_id: str = "0", trace: Path | None = None) -> Path:
    """Convert pptx -> pdf; retry once; raise RenderError with a clear message on failure."""
    pptx, out_dir = pptx.resolve(), out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = out_dir / (pptx.stem + ".pdf")
    pdf.unlink(missing_ok=True)
    mode = backend()
    last = ""
    for _attempt in (1, 2):
        if mode == "local":
            profile = Path(tempfile.gettempdir()) / f"lo_profile_{worker_id}"
            cmd = [_local_soffice(), *_soffice_args(profile, str(out_dir), str(pptx))]
        else:
            # mount the pptx dir and out dir under /in and /out
            cmd = ["docker", "run", "--rm", "-v", f"{pptx.parent}:/in:ro", "-v", f"{out_dir}:/out", CONFIG.docker_image,
                   "soffice", *_soffice_args_str(f"/tmp/lo_profile_{worker_id}", "/out", f"/in/{pptx.name}")]
        try:
            r = _run(cmd, CONFIG.soffice_timeout, trace)
            last = r.stderr
            if pdf.exists():
                return pdf
        except RenderError as e:
            last = str(e)
    raise RenderError(f"soffice failed after retry: {last.strip()[:300]}")


def _soffice_args_str(profile: str, outdir: str, src: str) -> list[str]:
    return ["--headless", "--invisible", "--norestore", "--nologo", "--nodefault",
            f"-env:UserInstallation=file://{profile}", "--convert-to", "pdf", "--outdir", outdir, src]


def pdf_to_images(pdf: Path, img_dir: Path, dpi: int | None = None, trace: Path | None = None) -> list[Path]:
    """Stale slide-*.jpg removed first; always glob (pdftoppm zero-pads by page count)."""
    dpi = dpi or CONFIG.render_dpi
    pdf, img_dir = pdf.resolve(), img_dir.resolve()
    img_dir.mkdir(parents=True, exist_ok=True)
    for old in img_dir.glob("slide-*.jpg"):
        old.unlink()
    if backend() == "local":
        cmd = ["pdftoppm", "-jpeg", "-r", str(dpi), str(pdf), str(img_dir / "slide")]
    else:
        cmd = ["docker", "run", "--rm", "-v", f"{pdf.parent}:/in:ro", "-v", f"{img_dir}:/out", CONFIG.docker_image,
               "pdftoppm", "-jpeg", "-r", str(dpi), f"/in/{pdf.name}", "/out/slide"]
    r = _run(cmd, CONFIG.soffice_timeout, trace)
    imgs = sorted(img_dir.glob("slide-*.jpg"), key=lambda p: int(p.stem.split("-")[1]))
    if r.returncode != 0 or not imgs:
        raise RenderError(f"pdftoppm failed: {r.stderr.strip()[:300]}")
    return imgs


def render_deck(pptx: Path, out_dir: Path, worker_id: str = "0") -> list[Path]:
    trace = out_dir / "render_trace.txt"
    pdf = pptx_to_pdf(pptx, out_dir, worker_id, trace)
    return pdf_to_images(pdf, out_dir / "images", trace=trace)


def list_fonts() -> str:
    """fc-list output from the render environment (fonts there decide QA accuracy)."""
    if backend() == "local":
        if not shutil.which("fc-list"):
            return ""  # ponytail: Windows has no fc-list; font check skipped locally
        return subprocess.run(["fc-list"], capture_output=True, text=True).stdout
    return subprocess.run(["docker", "run", "--rm", CONFIG.docker_image, "fc-list"], capture_output=True, text=True).stdout
