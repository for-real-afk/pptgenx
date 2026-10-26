// Helpers a deck build script may use. Every shape is made from scheme colours (ctx.C), fresh option objects, and text boxes with margin 0.
// ctx = helpers.ctx(pres, slide, C, theme). Rects are { x, y, w, h } in inches; the content area below the title is helpers.CONTENT.
const { CONTENT, MARGIN, GAP, W, H, SOURCE_Y, cols, rows, grid } = require("./grid");
const compose = require("./compose");
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

const cards = composed("cards", "{ icon, heading, body } (2, 3, 4 or 6)");
const stats = composed("stats", "{ value, label, detail? } (2 to 4)");
const timeline = composed("timeline", "{ year, heading, body } (3 to 5)");
const comparison = composed("comparison", "{ heading, bullets: string[] } (exactly 2)");
const icon_rows = composed("icon_rows", "{ icon, heading, body } (2 to 5)");

module.exports = { ctx, textBox, bullets, card, badge, stat, table, chart, notes, panel, iconCircle, cards, stats, timeline, comparison, icon_rows, CONTENT, MARGIN, GAP, W, H, SOURCE_Y, cols, rows, grid };
