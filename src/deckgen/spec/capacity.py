"""Text capacity per composition item, derived from the same geometry as renderer/grid.js and compose/*.js.
Edit content to fit the layout, never the reverse: these limits go into the spec prompt and the validator."""
from __future__ import annotations

CONTENT_W, CONTENT_H, GAP = 12.133, 4.85, 0.3
SAFETY = 0.9          # conservative: real glyph widths vary
EM, BOLD_EM, LINE = 0.5, 0.52, 1.2


def fit(w: float, h: float, pt: float, bold: bool = False) -> int:
    """Max characters that fit in a w x h inch box at pt points (word wrap loses a little: SAFETY)."""
    per_line = int(w * 72 / (pt * (BOLD_EM if bold else EM)))
    lines = max(1, int(h * 72 / (pt * LINE)))
    return int(per_line * lines * SAFETY)


def _cols(n: int) -> float:
    return (CONTENT_W - GAP * (n - 1)) / n


def limits(kind: str, n: int) -> dict[str, int]:
    """Field -> max characters, for one item of a composition with n items."""
    if kind in ("cards",):
        nr, nc = (1, n) if n <= 3 else ((2, 2) if n == 4 else (2, 3))
        w, h = _cols(nc) - 0.6, (CONTENT_H - GAP * (nr - 1)) / nr
        if nr == 2:  # heading beside the icon, body full width below (see compose/cards.js)
            return {"heading": fit(w - 0.95, 0.7, 18, True), "body": fit(w, h - 1.5, 14)}
        return {"heading": fit(w, 0.45, 18, True), "body": fit(w, min(h, 3.9) - 1.95, 16)}
    if kind == "stats":
        w = _cols(n) - 0.6
        return {"value": fit(w, 1.3, 60, True), "label": fit(w, 0.8, 18, True), "detail": fit(w, 0.8, 14)}
    if kind == "timeline":
        w = _cols(n) - 0.5
        return {"year": fit(_cols(n), 0.6, 28, True), "heading": fit(w, 0.9, 20, True), "body": fit(w, 1.95, 16)}
    if kind == "comparison":
        w = (CONTENT_W - GAP) / 2 - 0.8
        return {"heading": fit(w, 0.8, 26, True), "bullet": fit(w, 2 * 20 * LINE / 72, 20)}
    if kind == "icon_rows":
        row_h = (CONTENT_H - GAP * (n - 1)) / n
        return {"heading": fit(3.0, row_h, 22, True), "body": fit(CONTENT_W - 0.3 - 4.75, row_h, 18)}
    raise ValueError(kind)


def comparison_max_bullets() -> int:
    w_h = CONTENT_H - 0.8 - 1.1  # bullets box height
    return int(w_h / (2 * 20 * LINE / 72 + 14 / 72))  # two-line bullets at 20 pt with 14 pt spacing


def title_limit() -> int:
    return fit(CONTENT_W, 0.95, 32, True)


def commentary_limit() -> tuple[int, int]:
    """(max bullets, max chars per bullet) for the CHART_COMMENTARY body: 4.23 x 4.85 in at 16 pt, 14 pt spacing."""
    w, h = 4.23, 4.85
    per_line = int(w * 72 / (16 * EM))
    line_h = 16 * LINE / 72
    n = 4
    while n > 1 and n * (2 * line_h + 14 / 72) > h:
        n -= 1
    return n, int(per_line * 2 * SAFETY)
