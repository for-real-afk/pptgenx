const { rows } = require("../grid");
const { text, panel, iconCircle } = require("./common");

// items: [{icon, heading, body}] (3-5). One full-width panel per row.
async function iconRows(ctx, items, region) {
  const cells = rows(items.length, region);
  const d = Math.min(0.85, cells[0].h - 0.2), pad = 0.3, headW = 3.0; // circle never taller than its row
  for (const [i, it] of items.entries()) {
    const r = cells[i], n = `Row ${i + 1}`;
    panel(ctx, r, `${n} panel`);
    await iconCircle(ctx, it.icon, r.x + pad, r.y + (r.h - d) / 2, d, n);
    const hx = r.x + pad + d + 0.3;
    text(ctx, it.heading, { x: hx, y: r.y, w: headW, h: r.h, fontSize: 22, bold: true, color: ctx.C.text2, valign: "middle", objectName: `${n} heading` });
    text(ctx, it.body, { x: hx + headW + 0.3, y: r.y, w: r.x + r.w - pad - (hx + headW + 0.3), h: r.h, fontSize: 18, color: ctx.C.text1, valign: "middle", objectName: `${n} body` });
  }
}
module.exports = { iconRows };
