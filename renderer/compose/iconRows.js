const { rows } = require("../grid");
const { text, iconCircle } = require("./common");

// items: [{icon, heading, body}] (3-5)
async function iconRows(ctx, items, region) {
  const cells = rows(items.length, region, 0.2);
  const d = 0.75;
  for (const [i, it] of items.entries()) {
    const r = cells[i], n = `Row ${i + 1}`;
    const cy = r.y + (r.h - d) / 2;
    await iconCircle(ctx, it.icon, r.x, cy, d, n);
    text(ctx, it.heading, { x: r.x + d + 0.3, y: r.y, w: 2.6, h: r.h, fontSize: 18, bold: true, color: ctx.C.text2, valign: "middle", objectName: `${n} heading` });
    text(ctx, it.body, { x: r.x + d + 0.3 + 2.6 + GAPX, y: r.y, w: r.w - d - 0.3 - 2.6 - GAPX, h: r.h, fontSize: 16, color: ctx.C.text1, valign: "middle", objectName: `${n} body` });
  }
}
const GAPX = 0.3;
module.exports = { iconRows };
