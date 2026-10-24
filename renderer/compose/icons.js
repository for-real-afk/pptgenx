// react-icons -> SVG -> PNG (>=256px) -> base64 data URI with the prefix pptxgenjs needs.
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const sharp = require("sharp");

const cache = new Map();

async function iconPng(iconName, hexColor, px = 256) {
  const key = `${iconName}|${hexColor}|${px}`;
  if (cache.has(key)) return cache.get(key);
  const pack = iconName.slice(0, 2).toLowerCase();
  let Icon;
  try { Icon = require(`react-icons/${pack}`)[iconName]; } catch (e) { Icon = null; }
  if (!Icon) throw new Error(`icon "${iconName}" not found in react-icons/${pack}`);
  const svg = renderToStaticMarkup(React.createElement(Icon, { color: `#${hexColor}`, size: px }));
  const png = await sharp(Buffer.from(svg)).resize(px, px).png().toBuffer();
  const uri = "image/png;base64," + png.toString("base64");
  cache.set(key, uri);
  return uri;
}

module.exports = { iconPng };
