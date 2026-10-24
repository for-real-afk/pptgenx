// Shared geometry for every composed slide. Units: inches, LAYOUT_WIDE.
const W = 13.333, H = 7.5;
const MARGIN = 0.6;
const GAP = 0.3;
const CONTENT = { x: MARGIN, y: 1.65, w: W - 2 * MARGIN, h: 4.85 }; // below title, above source caption
const SOURCE_Y = 6.7;

/** Split `region` into n equal columns separated by GAP. */
function cols(n, region = CONTENT, gap = GAP) {
  const w = (region.w - gap * (n - 1)) / n;
  return Array.from({ length: n }, (_, i) => ({ x: region.x + i * (w + gap), y: region.y, w, h: region.h }));
}

/** Split `region` into n equal rows separated by GAP. */
function rows(n, region = CONTENT, gap = GAP) {
  const h = (region.h - gap * (n - 1)) / n;
  return Array.from({ length: n }, (_, i) => ({ x: region.x, y: region.y + i * (h + gap), w: region.w, h }));
}

/** rows x cols grid of cells, row-major. */
function grid(nRows, nCols, region = CONTENT, gap = GAP) {
  return rows(nRows, region, gap).flatMap((r) => cols(nCols, r, gap));
}

module.exports = { W, H, MARGIN, GAP, CONTENT, SOURCE_Y, cols, rows, grid };
