// Helpers a deck build script may use. Every shape is made from scheme colours (ctx.C), fresh option objects, and text boxes with margin 0.
// ctx = helpers.ctx(pres, slide, C, theme). Rects are { x, y, w, h } in inches; the content area below the title is helpers.CONTENT.
const { CONTENT, MARGIN, GAP, W, H, SOURCE_Y, cols, rows, grid } = require("./grid");
const compose = require("./compose/fit");        // content-fitted compositions (the fixed-size ones in compose/index.js serve the staged pipeline)
const { textH } = compose;
const { text, panel, iconCircle } = require("./compose/common");
const { addChart, addTable } = require("./build_deck");

const ctx = (pres, slide, C, theme) => ({ pres, slide, C, theme });

// Clear argument errors: a build script that passes the wrong shape gets a sentence that names the helper and the expected shape, not "undefined.length".
const need = (ok, name, shape) => { if (!ok) throw new Error(`h.${name}: ${shape}`); };
const isCtx = (c) => c && c.slide && c.pres && c.C;
const isRect = (r) => r && [r.x, r.y, r.w, r.h].every(Number.isFinite);
const composed = (name, itemShape) => async (c, items, region = CONTENT) => {
  need(isCtx(c), name, "first argument must be the context from h.ctx(pres, slide, C, theme)");
  need(Array.isArray(items) && items.length > 0, name, `items must be a non-empty array of ${itemShape}`);
  need(isRect(region), name, "region must be a rect { x, y, w, h } in inches (default h.CONTENT)");
  return compose[name](c, items, region);
};

/** Text box with margin 0 (pptxgenjs default margins misalign text with shapes). */
const textBox = (c, str, o) => text(c, str, { fontSize: 16, color: c.C.text1, valign: "top", ...o });

/** Bulleted list; every item breaks the line, which pptxgenjs needs to render separate bullets. */
const bullets = (c, items, o) =>
  c.slide.addText(items.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < items.length - 1, paraSpaceAfter: 10 } })),
    { isTextBox: true, margin: 0, fontSize: 16, color: c.C.text1, valign: "top", ...o });

/** Rounded panel with a heading and body text. opts.icon adds an icon circle. */
async function card(c, r, { heading, body, icon }, name = "Card") {
  panel(c, r, `${name} panel`);
  const pad = 0.3;
  let y = r.y + pad;
  if (icon) { await iconCircle(c, icon, r.x + pad, y, 0.7, name); y += 0.9; }
  textBox(c, heading, { x: r.x + pad, y, w: r.w - 2 * pad, h: 0.5, fontSize: 18, bold: true, color: c.C.text2, objectName: `${name} heading` });
  textBox(c, body, { x: r.x + pad, y: y + 0.55, w: r.w - 2 * pad, h: r.y + r.h - pad - (y + 0.55), objectName: `${name} body` });
}

/** Small filled label (a tag such as a year or a status). */
function badge(c, str, x, y, w = 1.4, name = "Badge") {
  c.slide.addShape(c.pres.ShapeType.roundRect, { x, y, w, h: 0.4, rectRadius: 0.2, fill: { color: c.C.accent1 }, line: { type: "none" }, objectName: `${name} shape` });
  textBox(c, str, { x, y, w, h: 0.4, fontSize: 14, bold: true, color: c.C.background1, align: "center", valign: "middle", objectName: `${name} text` });
}

/** One big number with a label and optional detail, on a panel. */
function stat(c, r, { value, label, detail }, name = "Stat") {
  panel(c, r, `${name} panel`);
  const pad = 0.3;
  textBox(c, value, { x: r.x + pad, y: r.y + 0.4, w: r.w - 2 * pad, h: 1.2, fontSize: 54, bold: true, color: c.C.accent2, valign: "middle", fit: "none", objectName: `${name} value` });
  textBox(c, label, { x: r.x + pad, y: r.y + 1.7, w: r.w - 2 * pad, h: 0.8, fontSize: 18, bold: true, color: c.C.text2, objectName: `${name} label` });
  if (detail) textBox(c, detail, { x: r.x + pad, y: r.y + 2.55, w: r.w - 2 * pad, h: Math.max(0.6, r.h - 2.55 - pad), fontSize: 14, objectName: `${name} detail` });
}

/** Styled native table. header: [..], body: [[..]]; cells <= ~34 characters; at most 6 rows. */
const table = (c, header, body, r = CONTENT) => {
  need(isCtx(c) && Array.isArray(header) && Array.isArray(body) && body.every(Array.isArray), "table", "table(c, header: string[], rows: string[][], rect = CONTENT)");
  return addTable(c.slide, c.pres.SchemeColor, { header, rows: body }, r);
};

/** Styled native chart. type: bar | column | line | pie | doughnut; series: [{ name, values }]. */
const chart = (c, type, title, categories, series, r = CONTENT, numberFormat) => {
  need(isCtx(c) && ["bar", "column", "line", "pie", "doughnut"].includes(type) && Array.isArray(categories) && Array.isArray(series) && series.every((s) => Array.isArray(s.values)),
    "chart", "chart(c, type: bar|column|line|pie|doughnut, title: string, categories: string[], series: [{ name, values: number[] }], rect = CONTENT)");
  return addChart(c.slide, c.pres, c.theme, { type, title, categories, series, number_format: numberFormat }, r);
};

/** Speaker notes. Name the source of each claim on the slide. */
const notes = (c, str) => c.slide.addNotes(str);

/** Slide title in the title placeholder: 36 pt for a short title, 32 pt otherwise (shorten the text rather than shrinking further). */
const title = (slide, str) => {
  need(typeof str === "string" && str.length > 0, "title", "title(slide, text: string)");
  slide.addText(str, { placeholder: "title", fontSize: str.length <= 46 ? 36 : 32 });
};

/** Chart slide on the CHART_COMMENTARY layout: the chart takes about 65% of the width, commentary beside it at 16 pt, an optional 12 pt caption under the chart. */
function chartSlide(c, { type, title: chartTitle, categories, series, bullets: points, caption, numberFormat }) {
  need(isCtx(c) && Array.isArray(points) && points.length > 0, "chartSlide", "chartSlide(c, { type, title, categories, series, bullets: string[], caption?: string })");
  const chartRect = { x: MARGIN, y: CONTENT.y, w: 7.9, h: caption ? 4.3 : 4.85 };
  chart(c, type, chartTitle, categories, series, chartRect, numberFormat);
  if (caption) textBox(c, caption, { x: MARGIN, y: CONTENT.y + 4.4, w: 7.9, h: 0.4, fontSize: 12, color: c.C.text1, objectName: "Chart caption" });
  c.slide.addText(points.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < points.length - 1, paraSpaceAfter: 14 } })),
    { isTextBox: true, margin: 0, x: 8.9, y: CONTENT.y, w: 3.83, h: 4.85, fontSize: 16, color: c.C.text1, valign: "middle", objectName: "Commentary" });
}

/** Closing slide body: bullets at 24 pt on the dark layout, in a box that is vertically centred in the area left of the brand motif. Use 3 bullets. */
const closing = (c, points) => {
  need(isCtx(c) && Array.isArray(points) && points.length > 0, "closing", "closing(c, bullets: string[]) with c = h.ctx(pres, slide, C, theme)");
  c.slide.addText(points.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < points.length - 1, paraSpaceAfter: 22 } })),
    { isTextBox: true, margin: 0, x: MARGIN, y: 2.4, w: 8.2, h: 4.2, fontSize: 24, color: c.C.background1, valign: "middle", objectName: "Closing bullets" });
};

/** Sources in two columns at 16 pt: full title, then URL and tier. entries: [{ title, url, tier }]. Throws if they do not fit. */
function sources(c, entries) {
  need(isCtx(c) && Array.isArray(entries) && entries.every((e) => e.title && e.url), "sources", "sources(c, [{ title, url, tier }])");
  const colW = 5.9, top = CONTENT.y, maxH = SOURCE_Y - top;   // below the title box, above the footer
  const heightOf = (e) => textH(e.title, 16, colW) + textH(`${e.url} (tier ${e.tier})`, 16, colW) + 0.22;
  const columns = [[], []];
  let used = 0, col = 0;
  for (const e of entries) {
    const hh = heightOf(e);
    if (used + hh > maxH && col === 0) { col = 1; used = 0; }
    if (used + hh > maxH) throw new Error(`h.sources: ${entries.length} sources do not fit in two columns at 16 pt; keep the sources the deck actually used`);
    columns[col].push(e);
    used += hh;
  }
  columns.forEach((list, k) => {
    if (!list.length) return;
    c.slide.addText(list.flatMap((e, i) => [
      { text: e.title, options: { bold: true, breakLine: true } },
      { text: `${e.url} (tier ${e.tier})`, options: { color: c.C.accent3, breakLine: i < list.length - 1, paraSpaceAfter: 12 } },
    ]), { isTextBox: true, margin: 0, x: MARGIN + k * (colW + 0.33), y: top, w: colW, h: maxH, fontSize: 16, color: c.C.text1, valign: "top", objectName: `Sources column ${k + 1}` });
  });
}

const cards = composed("cards", "{ icon, heading, body } (2, 3, 4 or 6)");
const stats = composed("stats", "{ value, label, detail? } (2 to 4)");
const timeline = composed("timeline", "{ year, heading, body } (3 to 5)");
const comparison = composed("comparison", "{ heading, bullets: string[] } (exactly 2)");
const icon_rows = composed("icon_rows", "{ icon, heading, body } (2 to 5)");

module.exports = { ctx, textBox, bullets, card, badge, stat, table, chart, notes, title, chartSlide, closing, sources, panel, iconCircle, cards, stats, timeline, comparison, icon_rows, CONTENT, MARGIN, GAP, W, H, SOURCE_Y, cols, rows, grid };
