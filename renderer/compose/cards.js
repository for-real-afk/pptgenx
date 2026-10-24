const { grid, GAP } = require("../grid");
const { text, panel, iconCircle } = require("./common");

// items: [{icon, heading, body}] (3, 4 or 6). 3 -> 1x3, 4 -> 2x2, 6 -> 2x3.
async function cards(ctx, items, region) {
  const [nr, nc] = items.length <= 3 ? [1, items.length] : items.length === 4 ? [2, 2] : [2, 3];
  const cells = grid(nr, nc, nr === 1 ? { ...region, h: Math.min(region.h, 3.9) } : region);
  const pad = 0.3, d = 0.7;
  for (const [i, it] of items.entries()) {
    const r = cells[i], n = `Card ${i + 1}`;
    panel(ctx, r, `${n} panel`);
    await iconCircle(ctx, it.icon, r.x + pad, r.y + pad, d, n);
    if (nr === 2) {
      // two-row grids are short: heading sits beside the icon, body gets the full card width below
      const hx = r.x + pad + d + 0.25, by = r.y + pad + d + 0.2;
      text(ctx, it.heading, { x: hx, y: r.y + pad, w: r.x + r.w - pad - hx, h: d, fontSize: 18, bold: true, color: ctx.C.text2, valign: "middle", objectName: `${n} heading` });
      text(ctx, it.body, { x: r.x + pad, y: by, w: r.w - 2 * pad, h: r.y + r.h - pad - by, fontSize: 14, color: ctx.C.text1, valign: "top", objectName: `${n} body` });
      continue;
    }
    const ty = r.y + pad + d + GAP / 2;
    text(ctx, it.heading, { x: r.x + pad, y: ty, w: r.w - 2 * pad, h: 0.45, fontSize: 18, bold: true, color: ctx.C.text2, valign: "top", objectName: `${n} heading` });
    text(ctx, it.body, { x: r.x + pad, y: ty + 0.5, w: r.w - 2 * pad, h: r.y + r.h - pad - (ty + 0.5), fontSize: 16, color: ctx.C.text1, valign: "top", objectName: `${n} body` });
  }
}
module.exports = { cards };
