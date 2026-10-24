const { cols } = require("../grid");
const { text, panel } = require("./common");

// items: [{heading, bullets[]}] exactly 2
async function comparison(ctx, items, region) {
  const cells = cols(2, { ...region, h: Math.min(region.h, 3.6) });
  const pad = 0.35;
  items.forEach((it, i) => {
    const r = cells[i], n = `Column ${i + 1}`;
    panel(ctx, r, `${n} panel`);
    text(ctx, it.heading, { x: r.x + pad, y: r.y + pad, w: r.w - 2 * pad, h: 0.6, fontSize: 22, bold: true, color: i === 0 ? ctx.C.accent1 : ctx.C.accent2, valign: "middle", objectName: `${n} heading` });
    ctx.slide.addText(
      it.bullets.map((b, k) => ({ text: b, options: { bullet: true, breakLine: k < it.bullets.length - 1, paraSpaceAfter: 8 } })),
      { isTextBox: true, margin: 0, x: r.x + pad, y: r.y + pad + 0.8, w: r.w - 2 * pad, h: r.h - 2 * pad - 0.8, fontSize: 16, color: ctx.C.text1, valign: "top", objectName: `${n} bullets` },
    );
  });
}
module.exports = { comparison };
