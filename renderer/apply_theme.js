// pptxgenjs cannot write theme colors/name. Patch every ppt/theme/theme*.xml after saving.
// Throws BEFORE changing anything if a hex-only option received a scheme name (invalid srgbClr val).
const fs = require("fs");
const JSZip = require("jszip");

const SLOTS = ["dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "hlink", "folHlink"];
const HEX = /^[0-9A-Fa-f]{6}$/;

async function applyTheme(file, theme) {
  const zip = await JSZip.loadAsync(fs.readFileSync(file));
  for (const name of Object.keys(zip.files).filter((n) => n.endsWith(".xml"))) {
    const xml = await zip.file(name).async("string");
    for (const m of xml.matchAll(/<a:srgbClr val="([^"]*)"/g)) {
      if (!HEX.test(m[1])) throw new Error(`apply_theme: scheme color "${m[1]}" used in hex-only option (${name}); use hex from THEME`);
    }
  }
  for (const s of SLOTS) if (!HEX.test(theme.colors[s] || "")) throw new Error(`apply_theme: theme.colors.${s} must be 6-digit hex`);

  for (const name of Object.keys(zip.files).filter((n) => /^ppt\/theme\/theme\d+\.xml$/.test(n))) {
    let xml = await zip.file(name).async("string");
    for (const slot of SLOTS) {
      xml = xml.replace(new RegExp("<a:" + slot + ">[^]*?</a:" + slot + ">"), `<a:${slot}><a:srgbClr val="${theme.colors[slot].toUpperCase()}"/></a:${slot}>`);
    }
    xml = xml.replace(/(<a:theme\b[^>]*\bname=")[^"]*"/, `$1${theme.name}"`)
             .replace(/(<a:clrScheme name=")[^"]*"/, `$1${theme.name}"`);
    zip.file(name, xml);
  }
  const buf = await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" });
  fs.writeFileSync(file, buf);
}

module.exports = { applyTheme };
