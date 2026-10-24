const { cols } = require("../grid");
const { text } = require("./common");

// items: [{year, heading, body}] (3-5). Horizontal rail, one marker per event.
async function timeline(ctx, items, region) {
  const cells = cols(items.length, region);
  const railY = region.y + 1.0, d = 0.32;
  ctx.slide.addShape(ctx.pres.ShapeType.rect, {
    x: region.x, y: railY - 0.02, w: region.w, h: 0.04, fill: { color: ctx.C.accent3 }, line: { type: "none" }, objectName: "Timeline rail",
  });
  items.forEach((it, i) => {
    const r = cells[i], n = `Event ${i + 1}`;
    text(ctx, it.year, { x: r.x, y: region.y, w: r.w, h: 0.55, fontSize: 24, bold: true, color: ctx.C.accent2, valign: "middle", objectName: `${n} year` });
    ctx.slide.addShape(ctx.pres.ShapeType.ellipse, { x: r.x, y: railY - d / 2, w: d, h: d, fill: { color: ctx.C.accent1 }, line: { color: ctx.C.background1, width: 2 }, objectName: `${n} marker` });
    text(ctx, it.heading, { x: r.x, y: railY + 0.4, w: r.w, h: 0.9, fontSize: 16, bold: true, color: ctx.C.text2, valign: "top", objectName: `${n} heading` });
    text(ctx, it.body, { x: r.x, y: railY + 1.35, w: r.w, h: region.y + region.h - (railY + 1.35), fontSize: 14, color: ctx.C.text1, valign: "top", objectName: `${n} body` });
  });
}
module.exports = { timeline };
