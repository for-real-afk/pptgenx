// usage: node run_script.js <script.js> <brandDir> <out.pptx> <allowedDir> [<allowedDir>...]
// Runs a deck build script the agent wrote, inside the brand frame, in a restricted context:
//   - the script runs in a fresh vm context that has no process, no global require and no network;
//   - `require` only serves "path", "./helpers" (renderer/helpers.js) and a read-only `fs` that can only touch the allowed directories;
//   - the script must `module.exports = async ({ pres, C, theme, h, W, H, MARGIN, CONTENT }) => { ... }`.
// After the script returns, the deck is written and the brand theme is applied. The last stdout line is JSON: { ok, slides } or { ok: false, error, line }.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { newDeck } = require("./build_deck");
const { applyTheme } = require("./apply_theme");
const { pruneEmptyPlaceholders } = require("./prune");
const helpers = require("./helpers");

const BLOCKED = /^(child_process|net|http|https|http2|dgram|dns|tls|worker_threads|cluster|vm|os|inspector)$/;
const TIME_LIMIT_MS = 90_000;

function guardedFs(allowed) {
  const inside = (p) => {
    const real = path.resolve(String(p));
    if (!allowed.some((d) => real === d || real.startsWith(d + path.sep))) throw new Error(`file access outside the allowed folders is blocked: ${p}`);
    return real;
  };
  return {
    readFileSync: (p, o) => fs.readFileSync(inside(p), o),
    existsSync: (p) => { try { return fs.existsSync(inside(p)); } catch { return false; } },
    readdirSync: (p) => fs.readdirSync(inside(p)),
    // no write functions: a build script produces its deck through pptxgenjs only
  };
}

function sandboxRequire(allowed) {
  const gfs = guardedFs(allowed);
  return (name) => {
    if (name === "fs") return gfs;
    if (name === "path") return path;
    if (name === "./helpers" || name === "helpers") return helpers;
    if (BLOCKED.test(name)) throw new Error(`module "${name}" is not available in a build script`);
    throw new Error(`module "${name}" is not available; use the injected h (helpers), pres and C`);
  };
}

function lineOf(err) {
  const m = /build_script\.js:(\d+)/.exec(String(err && err.stack));
  return m ? Number(m[1]) : null;
}

async function main() {
  const [script, brandDir, out, ...dirs] = process.argv.slice(2);
  const allowed = dirs.map((d) => path.resolve(d));
  const code = fs.readFileSync(script, "utf8");
  const { pres, C, theme } = await newDeck(brandDir, "Deck");
  const mod = { exports: {} };
  const context = vm.createContext({ console: { log() {}, error() {}, warn() {} }, module: mod, exports: mod.exports, require: sandboxRequire(allowed), __dirname: allowed[0] || "." });
  try {
    new vm.Script(code, { filename: "build_script.js" }).runInContext(context, { timeout: 5000 });
    if (typeof mod.exports !== "function") throw new Error("the script must set module.exports to an async function ({ pres, C, theme, h, W, H, MARGIN, CONTENT }) => { ... }");
    const run = mod.exports({ pres, C, theme, h: helpers, W: helpers.W, H: helpers.H, MARGIN: helpers.MARGIN, CONTENT: helpers.CONTENT });
    let timer;
    const limit = new Promise((_, rej) => { timer = setTimeout(() => rej(new Error(`the script took longer than ${TIME_LIMIT_MS / 1000}s`)), TIME_LIMIT_MS); });
    try { await Promise.race([run, limit]); } finally { clearTimeout(timer); }
  } catch (e) {
    console.log(JSON.stringify({ ok: false, error: String((e && e.message) || e).slice(0, 400), line: lineOf(e) }));
    process.exit(1);
  }
  await pres.writeFile({ fileName: out });
  await pruneEmptyPlaceholders(out);
  await applyTheme(out, theme);
  console.log(JSON.stringify({ ok: true, slides: pres.slides.length }));
  process.exit(0);
}

main().catch((e) => {
  console.log(JSON.stringify({ ok: false, error: String((e && e.message) || e).slice(0, 400), line: null }));
  process.exit(1);
});
