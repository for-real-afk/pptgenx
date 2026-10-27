const { iconPng } = require("./icons");

// Fresh option objects every call: pptxgenjs mutates them in place.
const text = (ctx, str, o) => ctx.slide.addText(str, { isTextBox: true, margin: 0, ...o });

const panel = (ctx, r, name) =>
  ctx.slide.addShape(ctx.pres.ShapeType.roundRect, {
    x: r.x, y: r.y, w: r.w, h: r.h, rectRadius: 0.12,
    fill: { color: ctx.C.background2 }, line: { type: "none" }, objectName: name,
  });

/** Motif: icon in a colored circle (works on light and dark). */
async function iconCircle(ctx, icon, x, y, d, name, alt) {
  ctx.slide.addShape(ctx.pres.ShapeType.ellipse, {
    x, y, w: d, h: d, fill: { color: ctx.C.accent1 }, line: { type: "none" }, objectName: `${name} circle`,
  });
  const pad = d * 0.25;
  ctx.slide.addImage({
    data: await iconPng(icon, ctx.theme.colors.lt1), x: x + pad, y: y + pad, w: d - 2 * pad, h: d - 2 * pad,
    altText: alt ? `Icon: ${alt}` : `${icon} icon`, objectName: `${name} icon`,
  });
}

module.exports = { text, panel, iconCircle };
