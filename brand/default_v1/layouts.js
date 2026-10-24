// Brand layouts: data first (so manifest.json can be generated), then defineAll() registers them.
// A layout is only the frame. Footer text + slide number live here, never on slides.
const { W, H, MARGIN } = require("../../renderer/grid");

const TITLE_BOX = { x: MARGIN, y: 0.45, w: W - 2 * MARGIN, h: 0.95 };

const LAYOUTS = [
  {
    name: "TITLE", dark: true,
    use_when: "Opening slide: deck title and one-line subtitle.",
    placeholders: [
      { name: "title", type: "title", x: MARGIN, y: 2.3, w: 10.5, h: 1.8, fontSize: 48, bold: true, color: "bg1", valign: "bottom" },
      { name: "subtitle", type: "body", x: MARGIN, y: 4.3, w: 10.5, h: 1.0, fontSize: 20, color: "bg2", valign: "top" },
    ],
  },
  {
    name: "SECTION", dark: true,
    use_when: "Section divider between major parts of a long deck.",
    placeholders: [
      { name: "title", type: "title", x: MARGIN, y: 2.7, w: 10.5, h: 1.3, fontSize: 40, bold: true, color: "bg1", valign: "bottom" },
      { name: "subtitle", type: "body", x: MARGIN, y: 4.2, w: 10.5, h: 0.9, fontSize: 18, color: "bg2", valign: "top" },
    ],
  },
  {
    name: "TITLE_ONLY", dark: false,
    use_when: "Default content frame; compose cards, stats, timeline, comparison or icon rows below the title.",
    placeholders: [
      { name: "title", type: "title", ...TITLE_BOX, fontSize: 32, bold: true, color: "tx2", valign: "middle" },
    ],
  },
  {
    name: "CHART_COMMENTARY", dark: false,
    use_when: "One native chart on the left with 2-4 commentary bullets on the right.",
    placeholders: [
      { name: "title", type: "title", ...TITLE_BOX, fontSize: 32, bold: true, color: "tx2", valign: "middle" },
      { name: "chart", type: "chart", x: MARGIN, y: 1.65, w: 7.6, h: 4.85, color: "tx1" },
      { name: "body", type: "body", x: 8.5, y: 1.65, w: W - MARGIN - 8.5, h: 4.85, fontSize: 16, color: "tx1", valign: "middle" },
    ],
  },
  {
    name: "TABLE", dark: false,
    use_when: "A compact comparison table (max ~6 rows x 5 columns).",
    placeholders: [
      { name: "title", type: "title", ...TITLE_BOX, fontSize: 32, bold: true, color: "tx2", valign: "middle" },
      { name: "table", type: "tbl", x: MARGIN, y: 1.65, w: W - 2 * MARGIN, h: 4.85, color: "tx1" },
    ],
  },
  {
    name: "CLOSING", dark: true,
    use_when: "Conclusion and next steps; dark bookend.",
    placeholders: [
      { name: "title", type: "title", x: MARGIN, y: 0.9, w: W - 2 * MARGIN, h: 1.1, fontSize: 36, bold: true, color: "bg1", valign: "middle" },
      { name: "body", type: "body", x: MARGIN, y: 2.3, w: 10.5, h: 4.0, fontSize: 18, color: "bg1", valign: "top" },
    ],
  },
  {
    name: "SOURCES", dark: false,
    use_when: "Final slide listing sources actually used.",
    placeholders: [
      { name: "title", type: "title", ...TITLE_BOX, fontSize: 32, bold: true, color: "tx2", valign: "middle" },
      { name: "body", type: "body", x: MARGIN, y: 1.65, w: W - 2 * MARGIN, h: 4.9, fontSize: 12, color: "tx1", valign: "top" },
    ],
  },
];

function defineAll(pres, theme) {
  const C = pres.SchemeColor;
  const cm = { tx1: C.text1, tx2: C.text2, bg1: C.background1, bg2: C.background2 };
  for (const L of LAYOUTS) {
    const objects = L.placeholders.map((p) => {
      const { name, type, x, y, w, h, fontSize, bold, color, valign } = p;
      const options = { name, type, x, y, w, h, color: cm[color], align: "left" };
      if (type !== "chart" && type !== "tbl") Object.assign(options, { fontSize, bold: !!bold, valign, margin: 0 });
      return { placeholder: { options, text: "" } };
    });
    objects.push({
      text: { text: theme.footerText, options: { x: MARGIN, y: 7.0, w: 4, h: 0.3, fontSize: 10, color: L.dark ? C.background2 : C.accent3, margin: 0, isTextBox: true } },
    });
    pres.defineSlideMaster({
      title: L.name,
      background: { color: L.dark ? C.text2 : C.background1 },
      objects,
      slideNumber: { x: W - MARGIN - 0.6, y: 7.0, w: 0.6, h: 0.3, fontSize: 10, color: L.dark ? C.background2 : C.accent3, align: "right" },
    });
  }
}

module.exports = { LAYOUTS, defineAll };
