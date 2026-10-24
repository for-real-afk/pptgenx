const { cols } = require("../grid");
const { text, panel } = require("./common");

// items: [{value, label, detail?}] (2-4)
async function stats(ctx, items, region) {
  const cells = cols(items.length, { ...region, h: Math.min(region.h, 3.9) });
  const pad = 0.3;
  items.forEach((it, i) => {
    const r = cells[i], n = `Stat ${i + 1}`;
    panel(ctx, r, `${n} panel`);
    text(ctx, it.value, { x: r.x + pad, y: r.y + 0.5, w: r.w - 2 * pad, h: 1.3, fontSize: 60, bold: true, color: ctx.C.accent2, valign: "middle", fit: "none", objectName: `${n} value` });
    text(ctx, it.label, { x: r.x + pad, y: r.y + 1.95, w: r.w - 2 * pad, h: 0.8, fontSize: 18, bold: true, color: ctx.C.text2, valign: "top", objectName: `${n} label` });
    if (it.detail) text(ctx, it.detail, { x: r.x + pad, y: r.y + 2.8, w: r.w - 2 * pad, h: Math.max(0.6, r.h - 2.8 - pad), fontSize: 14, color: ctx.C.text1, valign: "top", objectName: `${n} detail` });
  });
}
module.exports = { stats };
