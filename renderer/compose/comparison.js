const { cols } = require("../grid");
const { text, panel } = require("./common");

// items: [{heading, bullets[]}] exactly 2. Panels fill the content area; type scales up to use it.
async function comparison(ctx, items, region) {
  const cells = cols(2, region);
  const pad = 0.4;
  items.forEach((it, i) => {
    const r = cells[i], n = `Column ${i + 1}`;
    panel(ctx, r, `${n} panel`);
    text(ctx, it.heading, { x: r.x + pad, y: r.y + pad, w: r.w - 2 * pad, h: 0.8, fontSize: 26, bold: true, color: ctx.C.accent1, valign: "middle", objectName: `${n} heading` });
    ctx.slide.addText(
      it.bullets.map((b, k) => ({ text: b, options: { bullet: true, breakLine: k < it.bullets.length - 1, paraSpaceAfter: 14 } })),
      { isTextBox: true, margin: 0, x: r.x + pad, y: r.y + pad + 1.1, w: r.w - 2 * pad, h: r.h - 2 * pad - 1.1, fontSize: 20, color: ctx.C.text1, valign: "top", objectName: `${n} bullets` },
    );
  });
}
module.exports = { comparison };
