// After pptxgenjs writes a deck, every slide still carries an (empty) placeholder for each layout placeholder the script did not fill.
// PowerPoint shows those as "Click to add text" in edit view. This removes slide-level placeholder shapes that hold no text.
const fs = require("fs");
const JSZip = require("jszip");

const SHAPE = /<p:sp>(?:(?!<\/p:sp>)[^])*?<\/p:sp>/g;
const IS_PLACEHOLDER = /<p:ph\b/;
const HAS_TEXT = /<a:t>[^<]+<\/a:t>/;

async function pruneEmptyPlaceholders(file) {
  const zip = await JSZip.loadAsync(fs.readFileSync(file));
  let removed = 0;
  for (const name of Object.keys(zip.files).filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n))) {
    const xml = await zip.file(name).async("string");
    const out = xml.replace(SHAPE, (shape) => {
      if (IS_PLACEHOLDER.test(shape) && !HAS_TEXT.test(shape)) { removed += 1; return ""; }
      return shape;
    });
    if (out !== xml) zip.file(name, out);
  }
  if (removed) fs.writeFileSync(file, await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" }));
  return removed;
}

module.exports = { pruneEmptyPlaceholders };
