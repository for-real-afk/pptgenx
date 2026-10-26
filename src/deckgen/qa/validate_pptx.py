"""File QA: structural checks for things PowerPoint rejects that LibreOffice/python-pptx accept.
Fix findings in the generator and rebuild; never hand-edit packed XML."""
from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path

from defusedxml import ElementTree as ET
from pptx import Presentation

PRES_ORDER = ["sldMasterIdLst", "notesMasterIdLst", "handoutMasterIdLst", "sldIdLst", "sldSz", "notesSz",
              "smartTags", "embeddedFontLst", "custShowLst", "photoAlbum", "custDataLst", "kinsoku",
              "defaultTextStyle", "modifyVerifier", "extLst"]
HEX = re.compile(r"^[0-9A-Fa-f]{6}$")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def validate(pptx: Path, layout_names: set[str] | None = None) -> list[str]:
    errs: list[str] = []
    try:
        z = zipfile.ZipFile(pptx)
    except zipfile.BadZipFile as e:
        return [f"not a zip: {e}"]
    if (bad := z.testzip()):
        errs.append(f"corrupt zip member {bad}")
    names = set(z.namelist())

    # content types cover every part
    ct = ET.fromstring(z.read("[Content_Types].xml"))
    defaults = {e.get("Extension").lower() for e in ct if _local(e.tag) == "Default"}
    overrides = {e.get("PartName") for e in ct if _local(e.tag) == "Override"}
    for n in names:
        if n.endswith("/") or n == "[Content_Types].xml":
            continue
        if "/" + n not in overrides and n.rsplit(".", 1)[-1].lower() not in defaults:
            errs.append(f"no content type for {n}")
    for o in overrides:
        # baseline: pptxgenjs lists an override per layout for slideMaster parts it never writes; PowerPoint ignores them
        if o.lstrip("/") not in names and "/slideMasters/slideMaster" not in o:
            errs.append(f"content type override for missing part {o}")

    xml = {}
    for n in names:
        if n.endswith((".xml", ".rels")):
            try:
                xml[n] = ET.fromstring(z.read(n))
            except Exception as e:  # malformed or unescaped text
                errs.append(f"{n}: not well-formed ({str(e)[:80]})")

    # relationship targets exist
    for n, root in xml.items():
        if not n.endswith(".rels"):
            continue
        base = posixpath.dirname(posixpath.dirname(n))
        for r in root:
            if r.get("TargetMode") == "External":
                continue
            t = r.get("Target")
            target = t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.join(base, t))
            if target not in names:
                errs.append(f"{n}: dangling relationship -> {target}")

    # <p:presentation> child order
    pres = xml.get("ppt/presentation.xml")
    if pres is not None:
        # baseline: pptxgenjs writes notesMasterIdLst after sldIdLst; PowerPoint accepts it, so exclude it
        idx = [PRES_ORDER.index(_local(c.tag)) for c in pres if _local(c.tag) in PRES_ORDER and _local(c.tag) != "notesMasterIdLst"]
        if idx != sorted(idx):
            errs.append("ppt/presentation.xml children out of schema order")

    for n, root in xml.items():
        raw = z.read(n).decode("utf-8", "ignore") if n.endswith(".xml") else ""
        for m in re.finditer(r'<a:srgbClr val="([^"]*)"', raw):
            if not HEX.match(m.group(1)):
                errs.append(f"{n}: invalid color '{m.group(1)}' (scheme name or 8-digit hex in hex-only option)")
        if re.search(r'<a:(outer|inner)Shdw[^>]*\bdist="-', raw):
            errs.append(f"{n}: negative shadow offset")
        if n.startswith("ppt/charts/chart") and n.endswith(".xml"):
            errs += _chart_errors(n, raw, root)

    # slides: layouts exist; charts native
    try:
        prs = Presentation(pptx)
    except Exception as e:  # python-pptx is the second opinion: if it cannot open the deck, report it
        return errs + [f"python-pptx cannot open deck: {str(e)[:120]}"]
    for i, s in enumerate(prs.slides, 1):
        if layout_names is not None and s.slide_layout.name not in layout_names:
            errs.append(f"slide {i}: layout '{s.slide_layout.name}' not in brand manifest")
        for sh in s.shapes:
            if sh.shape_type == 13 and sh.name.lower().startswith("chart"):
                errs.append(f"slide {i}: chart '{sh.name}' is a picture, not native")
    return errs


def _chart_errors(name: str, raw: str, root) -> list[str]:
    errs = []
    if ("<c:grouping val=\"stacked\"" in raw or "<c:grouping val=\"percentStacked\"" in raw) and re.search(r'<c:dLblPos val="outEnd"', raw):
        errs.append(f"{name}: stacked chart with dLblPos outEnd (PowerPoint repairs the file)")
    n_val = len(re.findall(r"<c:valAx>", raw))
    n_cat = len(re.findall(r"<c:(catAx|dateAx)>", raw))
    if n_val != n_cat and (n_val > 2 or n_cat > 2):
        errs.append(f"{name}: unbalanced axes (valAx={n_val}, catAx={n_cat})")
    plots = len(re.findall(r"<c:(barChart|lineChart|areaChart|scatterChart)>", raw))
    if plots > 1 and not (n_val == 2 and n_cat == 2):
        errs.append(f"{name}: combo chart needs two valAx and two catAx (found {n_val}/{n_cat})")
    return errs
