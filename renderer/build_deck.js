// usage: node build_deck.js <spec.json> <brandDir> <out.pptx>
// Renderer owns geometry. Specs carry only content; layouts/compose functions place it.
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");
const { applyTheme } = require("./apply_theme");
const { build: buildManifest } = require("./gen_manifest");
const compose = require("./compose");
const { CONTENT, MARGIN, SOURCE_Y, W } = require("./grid");

const CHART_FONT = "+mn-lt"; // body theme font; pptxgenjs hard-codes Arial otherwise
const CHART_TYPES = { bar: "bar", column: "bar", line: "line", pie: "pie", doughnut: "doughnut" };

function loadBrand(brandDir) {
  const theme = JSON.parse(fs.readFileSync(path.join(brandDir, "theme.json"), "utf8"));
  const layouts = require(path.resolve(brandDir, "layouts.js"));
  const manifest = buildManifest(brandDir); // always regenerate: manifest can never drift from layouts.js
  return { theme, layouts, manifest };
}

/** Fail before building: wrong layout/placeholder names would silently misrender. */
function verifySpec(spec, manifest) {
  const errs = [];
  const byName = Object.fromEntries(manifest.layouts.map((l) => [l.name, l]));
  for (const s of spec.slides) {
    const L = byName[s.layout];
    if (!L) { errs.push(`${s.id}: unknown layout "${s.layout}"`); continue; }
    const known = new Set(L.placeholders.map((p) => p.name));
    for (const k of Object.keys(s.placeholders || {})) if (!known.has(k)) errs.push(`${s.id}: layout ${s.layout} has no placeholder "${k}"`);
    if (s.photo && !L.photo) errs.push(`${s.id}: photo on layout without a photo frame`);
    if (s.layout === "PHOTO_TEXT" && !s.photo) errs.push(`${s.id}: PHOTO_TEXT needs a photo`);
    if (s.chart && !known.has("chart")) errs.push(`${s.id}: chart on layout without chart placeholder`);
    if (s.table && !known.has("table")) errs.push(`${s.id}: table on layout without table placeholder`);
    if (s.composition && s.layout !== "TITLE_ONLY") errs.push(`${s.id}: composition requires TITLE_ONLY`);
    if (s.composition && !compose[s.composition.kind]) errs.push(`${s.id}: unknown composition "${s.composition.kind}"`);
    if (s.chart && !CHART_TYPES[s.chart.type]) errs.push(`${s.id}: unknown chart type "${s.chart.type}"`);
  }
  if (errs.length) throw new Error("spec verification failed:\n  " + errs.join("\n  "));
}

const asRuns = (v, space = 14) =>
  Array.isArray(v) ? v.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < v.length - 1, paraSpaceAfter: space } })) : v;

function addChart(slide, pres, theme, c, rect) {
  const hex = (k) => theme.colors[k];
  const multi = c.series.length > 1 || c.type === "pie" || c.type === "doughnut";
  const data = c.series.map((s) => ({ name: s.name, labels: c.categories, values: s.values }));
  const t = CHART_TYPES[c.type];
  const opts = {
    ...(rect ? { x: rect.x, y: rect.y, w: rect.w, h: rect.h } : { placeholder: "chart" }),
    showTitle: true, title: c.title, titleFontFace: CHART_FONT, titleFontSize: 14, titleColor: hex("dk2"),
    showValue: true, dataLabelFontFace: CHART_FONT, dataLabelFontSize: 12, dataLabelColor: hex("dk1"),
    dataLabelPosition: t === "line" ? "t" : t === "bar" ? "outEnd" : "bestFit",
    chartColors: [hex("accent1"), hex("accent2"), hex("accent3"), hex("accent4"), hex("accent5"), hex("accent6")],
    showLegend: multi, legendFontFace: CHART_FONT, legendFontSize: 12, legendColor: hex("dk1"), legendPos: "b",
  };
  if (t === "bar") Object.assign(opts, { barDir: c.type === "bar" ? "bar" : "col", barGapWidthPct: 60 });
    opts.valAxisMinVal = 0; // bars always start at zero: a truncated axis exaggerates differences
    if (c.type === "bar") opts.catAxisOrientation = "maxMin"; // first category on top
  if (t === "bar" || t === "line") {
    Object.assign(opts, {
      catAxisLabelColor: hex("dk1"), valAxisLabelColor: hex("dk1"),
      catAxisLabelFontFace: CHART_FONT, valAxisLabelFontFace: CHART_FONT, catAxisLabelFontSize: 12, valAxisLabelFontSize: 12,
      valGridLine: { color: hex("lt2"), size: 0.75 }, catGridLine: { style: "none" },
    });
    if (c.series.length === 1) opts.chartColors = [hex("accent1")];
  }
  if (c.number_format) Object.assign(opts, { dataLabelFormatCode: c.number_format, valAxisLabelFormatCode: c.number_format });
  slide.addChart(pres.charts[t.toUpperCase()], data, opts);
}

function addTable(slide, C, tbl, rect) {
  const r = rect || { x: MARGIN, y: CONTENT.y, w: W - 2 * MARGIN, h: CONTENT.h };
  const hdr = tbl.header.map((h) => ({ text: h, options: { bold: true, color: C.background1, fill: { color: C.accent1 }, fontSize: 14 } }));
  const body = tbl.rows.map((r, ri) => r.map((c) => ({ text: String(c), options: { fontSize: 14, color: C.text1, fill: { color: ri % 2 ? C.background2 : C.background1 } } })));
  slide.addTable([hdr, ...body], {
    ...(rect ? {} : { placeholder: "table" }), x: r.x, y: r.y, w: r.w, autoPageSlideStartY: r.y,
    border: { type: "solid", pt: 0.5, color: "D0D5DD" }, margin: [0.1, 0.15, 0.1, 0.15], valign: "middle", rowH: Math.min(0.85, r.h / (tbl.rows.length + 1)),
  });
}

/** A presentation with the brand frame applied: wide layout, theme fonts, every named master defined. */
async function newDeck(brandDir, title, motifIcon) {
  const { theme, layouts, manifest } = loadBrand(brandDir);
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";
  pres.theme = { headFontFace: theme.headFontFace, bodyFontFace: theme.bodyFontFace };
  pres.title = title;
  await layouts.defineAll(pres, theme, motifIcon);
  return { pres, C: pres.SchemeColor, theme, layouts, manifest };
}

async function buildDeck(spec, brandDir, outFile) {
  const { pres, C, theme, layouts, manifest } = await newDeck(brandDir, spec.title, spec.motif_icon);
  verifySpec(spec, manifest);

  const manifestDark = Object.fromEntries(manifest.layouts.map((l) => [l.name, l.dark]));
  let section = null;
  for (const s of spec.slides) {
    if (s.section !== section) { pres.addSection({ title: s.section }); section = s.section; }
    const slide = pres.addSlide({ masterName: s.layout, sectionTitle: s.section });
    slide.addText(s.title, { placeholder: "title" });
    for (const [name, v] of Object.entries(s.placeholders || {})) slide.addText(asRuns(v, s.layout === "SOURCES" ? 4 : 14), { placeholder: name });
    const ctx = { pres, slide, C, theme };
    if (s.composition) await compose[s.composition.kind](ctx, s.composition.items, CONTENT);
    if (s.chart) addChart(slide, pres, theme, s.chart);
    if (s.table) addTable(slide, C, s.table);
    if (s.photo) {
      const box = layouts.LAYOUTS.find((l) => l.name === s.layout).photo;
      slide.addImage({ path: s.photo.path, ...box, altText: s.photo.alt });
    }
    if (s.source_caption) {
      slide.addText(s.source_caption, { isTextBox: true, margin: 0, x: MARGIN, y: SOURCE_Y, w: W - 2 * MARGIN - 1, h: 0.25, fontSize: 10, color: manifestDark[s.layout] ? C.background2 : C.accent3, valign: "middle", objectName: "Source caption" });
    }
    if (s.speaker_notes) slide.addNotes(s.speaker_notes);
  }
  await pres.writeFile({ fileName: outFile });
  await applyTheme(outFile, theme);
}

if (require.main === module) {
  const [specPath, brandDir, out] = process.argv.slice(2);
  if (!specPath || !brandDir || !out) { console.error("usage: node build_deck.js <spec.json> <brandDir> <out.pptx>"); process.exit(2); }
  buildDeck(JSON.parse(fs.readFileSync(specPath, "utf8")), brandDir, out)
    .then(() => console.log(`wrote ${out}`))
    .catch((e) => { console.error(e.message); process.exit(1); });
}
module.exports = { buildDeck, verifySpec, newDeck, addChart, addTable };
