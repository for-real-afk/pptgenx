// Content-fitted compositions for agent-written decks. Card height is computed from the text a card holds (plus padding), the type is as large as fits
// (body >= 16 pt, headings >= 20 pt, stat numbers >= 54 pt), short text is vertically centred, and the block of cards is centred in the content area, so
// no card is left with a big empty region at the bottom. The fixed-size compositions in this folder are still used by the staged pipeline.
const { grid, cols, GAP } = require("../grid");
const { text, panel, iconCircle } = require("./common");

const EM = 0.6;           // average character width in ems (conservative: a little too wide, so estimates err toward taller text)
const LINE = 1.22;        // line height in ems
const FILL = 0.78;        // a card is at least this full: its content takes >= 78% of its height
const PAD = 0.3;
const MARKER = 0.7;

const ALT = {
  FaUsers: "group of people", FaBolt: "lightning bolt", FaShieldAlt: "shield", FaFlagCheckered: "chequered flag", FaTrophy: "trophy", FaCogs: "gears",
  FaHistory: "clock with a counter-clockwise arrow", FaBook: "open book", FaGlobe: "globe", FaCarSide: "car seen from the side",
};

/** Height in inches of `str` set at `pt` points in a box `w` inches wide. */
function textH(str, pt, w, bold = false) {
  const perLine = Math.max(1, Math.floor((w * 72) / (pt * EM * (bold ? 1.08 : 1))));
  const lines = String(str).split("\n").reduce((n, p) => n + Math.max(1, Math.ceil(p.length / perLine)), 0);
  return (lines * pt * LINE) / 72;
}

const markerKind = (it) => (it.icon ? "icon" : it.badge ? "badge" : null);

async function drawMarker(ctx, it, x, y, d, name) {
  if (it.icon) return iconCircle(ctx, it.icon, x, y, d, name, it.alt || ALT[it.icon] || it.icon.replace(/^Fa/, "").replace(/([A-Z])/g, " $1").trim().toLowerCase());
  ctx.slide.addShape(ctx.pres.ShapeType.ellipse, { x, y, w: d, h: d, fill: { color: ctx.C.accent1 }, line: { type: "none" }, objectName: `${name} badge` });
  text(ctx, String(it.badge), { x, y, w: d, h: d, fontSize: 20, bold: true, color: ctx.C.background1, align: "center", valign: "middle", objectName: `${name} badge number` });
}

/** The largest size whose tallest card still fits `maxH`; falls back to the smallest (the 16 pt floor). */
const pickSize = (sizes, needed, maxH) => sizes.find((s) => needed(s) <= maxH) || sizes[sizes.length - 1];

const centred = (region, h) => ({ ...region, y: region.y + Math.max(0, (region.h - h) / 2), h });

async function cards(ctx, items, region) {
  const [nr, nc] = items.length <= 3 ? [1, items.length] : items.length === 4 ? [2, 2] : [2, 3];
  const cells = grid(nr, nc, region);
  const w = cells[0].w - 2 * PAD;
  const headPt = (b) => Math.max(20, b + 2);
  const need = (b) => Math.max(...items.map((it) => 2 * PAD + (markerKind(it) ? MARKER + 0.2 : 0) + textH(it.heading, headPt(b), w, true) + 0.12 + textH(it.body, b, w)));
  const b = pickSize([20, 18, 16], need, cells[0].h);
  const H = need(b), cardH = Math.min(cells[0].h, H / FILL);
  const block = centred(region, nr * cardH + (nr - 1) * GAP);
  for (const [i, it] of items.entries()) {
    const r = { x: cells[i].x, y: block.y + Math.floor(i / nc) * (cardH + GAP), w: cells[i].w, h: cardH }, n = `Card ${i + 1}`;
    panel(ctx, r, `${n} panel`);
    let y = r.y + (cardH - (H - 2 * PAD)) / 2;
    if (markerKind(it)) { await drawMarker(ctx, it, r.x + PAD, y, MARKER, n); y += MARKER + 0.2; }
    const hh = textH(it.heading, headPt(b), w, true);
    text(ctx, it.heading, { x: r.x + PAD, y, w, h: hh, fontSize: headPt(b), bold: true, color: ctx.C.text2, valign: "top", objectName: `${n} heading` });
    text(ctx, it.body, { x: r.x + PAD, y: y + hh + 0.12, w, h: textH(it.body, b, w), fontSize: b, color: ctx.C.text1, valign: "top", objectName: `${n} body` });
  }
}

async function stats(ctx, items, region) {
  const cells = cols(items.length, region);
  const w = cells[0].w - 2 * PAD, valuePt = 60, valueH = 1.15;
  const need = (lp) => Math.max(...items.map((it) => 2 * PAD + valueH + 0.1 + textH(it.label, lp, w, true) + (it.detail ? 0.1 + textH(it.detail, 16, w) : 0)));
  const lp = pickSize([24, 22, 20], need, region.h), H = need(lp), cardH = Math.min(region.h, H / FILL);
  const block = centred(region, cardH);
  items.forEach((it, i) => {
    const r = { x: cells[i].x, y: block.y, w: cells[i].w, h: cardH }, n = `Stat ${i + 1}`;
    panel(ctx, r, `${n} panel`);
    let y = r.y + (cardH - (H - 2 * PAD)) / 2;
    text(ctx, it.value, { x: r.x + PAD, y, w, h: valueH, fontSize: valuePt, bold: true, color: ctx.C.accent2, valign: "middle", fit: "none", objectName: `${n} value` });
    y += valueH + 0.1;
    const lh = textH(it.label, lp, w, true);
    text(ctx, it.label, { x: r.x + PAD, y, w, h: lh, fontSize: lp, bold: true, color: ctx.C.text2, valign: "top", objectName: `${n} label` });
    if (it.detail) text(ctx, it.detail, { x: r.x + PAD, y: y + lh + 0.1, w, h: textH(it.detail, 16, w), fontSize: 16, color: ctx.C.text1, valign: "top", objectName: `${n} detail` });
  });
}

async function timeline(ctx, items, region) {
  const cells = cols(items.length, region);
  const w = cells[0].w - 2 * 0.25, yearH = 0.6, d = 0.32;
  const need = (b) => Math.max(...items.map((it) => 2 * 0.25 + textH(it.heading, Math.max(20, b + 2), w, true) + 0.1 + textH(it.body, b, w)));
  const avail = region.h - 0.95 - 0.45;
  const b = pickSize([22, 20, 18, 16], need, avail), H = need(b), cardH = Math.min(avail, H / FILL);
  const top = centred(region, 0.95 + 0.45 + cardH).y;
  const railY = top + 0.95, cardY = railY + 0.45;
  ctx.slide.addShape(ctx.pres.ShapeType.rect, { x: region.x, y: railY - 0.02, w: region.w, h: 0.04, fill: { color: ctx.C.accent3 }, line: { type: "none" }, objectName: "Timeline rail" });
  items.forEach((it, i) => {
    const r = cells[i], n = `Event ${i + 1}`, hp = Math.max(20, b + 2);
    text(ctx, it.year, { x: r.x, y: top, w: r.w, h: yearH, fontSize: 28, bold: true, color: ctx.C.accent2, valign: "middle", objectName: `${n} year` });
    ctx.slide.addShape(ctx.pres.ShapeType.ellipse, { x: r.x, y: railY - d / 2, w: d, h: d, fill: { color: ctx.C.accent1 }, line: { color: ctx.C.background1, width: 2 }, objectName: `${n} marker` });
    panel(ctx, { x: r.x, y: cardY, w: r.w, h: cardH }, `${n} panel`);
    const y = cardY + (cardH - (H - 0.5)) / 2, hh = textH(it.heading, hp, w, true);
    text(ctx, it.heading, { x: r.x + 0.25, y, w, h: hh, fontSize: hp, bold: true, color: ctx.C.text2, valign: "top", objectName: `${n} heading` });
    text(ctx, it.body, { x: r.x + 0.25, y: y + hh + 0.1, w, h: textH(it.body, b, w), fontSize: b, color: ctx.C.text1, valign: "top", objectName: `${n} body` });
  });
}

async function comparison(ctx, items, region) {
  const cells = cols(2, region);
  const w = cells[0].w - 2 * 0.4;
  const need = (b) => Math.max(...items.map((it) => 2 * 0.4 + 0.9 + it.bullets.reduce((s, t) => s + textH(t, b, w - 0.3) + 0.2, 0)));
  const b = pickSize([24, 22, 20, 18], need, region.h), H = need(b), panelH = Math.min(region.h, H / FILL);
  const top = centred(region, panelH).y;
  items.forEach((it, i) => {
    const r = { x: cells[i].x, y: top, w: cells[i].w, h: panelH }, n = `Column ${i + 1}`;
    panel(ctx, r, `${n} panel`);
    const y = r.y + (panelH - (H - 0.8)) / 2;
    text(ctx, it.heading, { x: r.x + 0.4, y, w, h: 0.8, fontSize: 26, bold: true, color: ctx.C.accent1, valign: "middle", objectName: `${n} heading` });
    ctx.slide.addText(it.bullets.map((t, k) => ({ text: t, options: { bullet: true, breakLine: k < it.bullets.length - 1, paraSpaceAfter: 14 } })),
      { isTextBox: true, margin: 0, x: r.x + 0.4, y: y + 0.9, w, h: Math.max(0.5, r.y + panelH - (y + 0.9) - 0.2), fontSize: b, color: ctx.C.text1, valign: "top", objectName: `${n} bullets` });
  });
}

async function iconRows(ctx, items, region) {
  const d = 0.8, pad = 0.3, headW = 3.2;
  const bodyW = region.w - 2 * pad - d - 0.3 - headW - 0.3;
  const need = (b) => Math.max(...items.map((it) => Math.max(d + 0.2, textH(it.body, b, bodyW) + 0.3, textH(it.heading, Math.max(20, b + 2), headW, true) + 0.3)));
  const b = pickSize([22, 20, 18, 16], (s) => items.length * need(s) + (items.length - 1) * GAP, region.h);
  const rowH = Math.min((region.h - (items.length - 1) * GAP) / items.length, Math.max(need(b), 1.0) / FILL);
  const top = centred(region, items.length * rowH + (items.length - 1) * GAP).y;
  for (const [i, it] of items.entries()) {
    const r = { x: region.x, y: top + i * (rowH + GAP), w: region.w, h: rowH }, n = `Row ${i + 1}`;
    panel(ctx, r, `${n} panel`);
    let hx = r.x + pad;
    if (markerKind(it)) { await drawMarker(ctx, it, r.x + pad, r.y + (rowH - d) / 2, d, n); hx += d + 0.3; }
    const hw = headW + (markerKind(it) ? 0 : d + 0.3);
    text(ctx, it.heading, { x: hx, y: r.y, w: hw, h: rowH, fontSize: Math.max(20, b + 2), bold: true, color: ctx.C.text2, valign: "middle", objectName: `${n} heading` });
    text(ctx, it.body, { x: hx + hw + 0.3, y: r.y, w: r.x + r.w - pad - (hx + hw + 0.3), h: rowH, fontSize: b, color: ctx.C.text1, valign: "middle", objectName: `${n} body` });
  }
}

module.exports = { cards, stats, timeline, comparison, icon_rows: iconRows, textH, centred };
