const { cols } = require("../grid");
const { text, panel } = require("./common");

// items: [{year, heading, body}] (3-5). Year row, rail with markers, then a card per event filling the area.
async function timeline(ctx, items, region) {
  const cells = cols(items.length, region);
  const railY = region.y + 0.95, d = 0.32, cardY = railY + 0.45;
  const cardH = region.y + region.h - cardY, pad = 0.25;
  ctx.slide.addShape(ctx.pres.ShapeType.rect, {
    x: region.x, y: railY - 0.02, w: region.w, h: 0.04, fill: { color: ctx.C.accent3 }, line: { type: "none" }, objectName: "Timeline rail",
  });
  items.forEach((it, i) => {
    const r = cells[i], n = `Event ${i + 1}`;
    text(ctx, it.year, { x: r.x, y: region.y, w: r.w, h: 0.6, fontSize: 28, bold: true, color: ctx.C.accent2, valign: "middle", objectName: `${n} year` });
    ctx.slide.addShape(ctx.pres.ShapeType.ellipse, { x: r.x, y: railY - d / 2, w: d, h: d, fill: { color: ctx.C.accent1 }, line: { color: ctx.C.background1, width: 2 }, objectName: `${n} marker` });
    panel(ctx, { x: r.x, y: cardY, w: r.w, h: cardH }, `${n} panel`);
    text(ctx, it.heading, { x: r.x + pad, y: cardY + pad, w: r.w - 2 * pad, h: 0.9, fontSize: 20, bold: true, color: ctx.C.text2, valign: "top", objectName: `${n} heading` });
    text(ctx, it.body, { x: r.x + pad, y: cardY + pad + 1.0, w: r.w - 2 * pad, h: cardH - 2 * pad - 1.0, fontSize: 16, color: ctx.C.text1, valign: "top", objectName: `${n} body` });
  });
}
module.exports = { timeline };
