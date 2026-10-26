// layouts.js -> manifest.json. max_chars estimated from box size and font size.
const fs = require("fs");
const path = require("path");

const AVG_CHAR_W = 0.5;   // em, Calibri/Cambria average
const LINE_H = 1.2;       // em

function maxChars(p) {
  if (!p.fontSize) return null;
  const perLine = Math.floor((p.w * 72) / (p.fontSize * AVG_CHAR_W));
  const lines = Math.floor((p.h * 72) / (p.fontSize * LINE_H));
  return perLine * lines;
}

function build(brandDir) {
  const { LAYOUTS: L } = require(path.resolve(brandDir, "layouts.js"));
  const manifest = {
    layouts: L.map((l) => ({
      name: l.name, dark: l.dark, photo: l.photo || null, use_when: l.use_when,
      placeholders: l.placeholders.map((p) => ({ name: p.name, type: p.type, font_size: p.fontSize || null, max_chars: maxChars(p) })),
    })),
  };
  fs.writeFileSync(path.join(brandDir, "manifest.json"), JSON.stringify(manifest, null, 2));
  return manifest;
}

if (require.main === module) {
  const dir = process.argv[2] || path.join(__dirname, "..", "brand", "default_v1");
  const m = build(dir);
  console.log(`manifest: ${m.layouts.length} layouts -> ${path.join(dir, "manifest.json")}`);
}
module.exports = { build };
