import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import crypto from "node:crypto";
import zlib from "node:zlib";

// Turns the `--mode matrix` build into what the Matrix AI needs inside the
// ViVeSecBox (E07). The ViVeSec frame socket only carries requests the page
// makes itself, so the browser must never fetch an asset of the app:
//
//   dist/matrix/app/manifest.json   label, version, sha256 of the bundle
//   dist/matrix/app/bundle.json     {html, script}: the prerendered shell and the
//                                   one client script, every asset a data: URL
//   dist/matrix/boot/               the bootloader the ViVeSec hosts (matrix_boot/
//                                   + icons), also as matrix-ai-boot-<label>.zip
//
// The tunnel gateway serves app/ (MATRIX_TUNNEL_APP_DIR); the bootloader fetches
// it through the frame socket and puts the shell and the script into its page.
// Every step checks the shape of the build output: a framework upgrade that
// changes it must fail here, not in the customer's browser.

const clientDir = path.resolve("dist/matrix-client/client");
const outDir = path.resolve("dist/matrix");
const bootSource = path.resolve("matrix_boot");

const fail = (message) => {
  console.error(`[matrix-bundle] ${message}`);
  process.exit(1);
};

if (!fs.existsSync(path.join(clientDir, "index.html"))) {
  fail(`${clientDir}/index.html not found — run \`vite build --mode matrix\` first.`);
}

const assetsDir = path.join(clientDir, "assets");
const assets = fs.readdirSync(assetsDir);
const scripts = assets.filter((name) => name.endsWith(".js"));
if (scripts.length !== 1) fail(`expected one client script, found ${scripts.join(", ")}`);
let script = fs.readFileSync(path.join(assetsDir, scripts[0]), "utf-8");
let html = fs.readFileSync(path.join(clientDir, "index.html"), "utf-8");
const build = JSON.parse(fs.readFileSync(path.join(clientDir, "version.json"), "utf-8"));

if (/\bimport\s*\(|^\s*import[\s{*]|[;}]import[\s{*]/m.test(script)) {
  fail("the client script still imports other modules — inlineDynamicImports is not in effect.");
}

const MIME = {
  ".css": "text/css",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
  ".woff": "font/woff",
  ".jpg": "image/jpeg",
  ".webp": "image/webp",
};
const dataUrl = (name) => {
  const type = MIME[path.extname(name)];
  if (!type) fail(`no data: URL type for asset ${name}`);
  const data = fs.readFileSync(path.join(assetsDir, name)).toString("base64");
  return `data:${type};base64,${data}`;
};

// Assets the running app resolves: vite.config.ts emits them as
// new URL("assets/<name>", document.baseURI).href.
const inlined = new Map();
script = script.replace(/new URL\("assets\/([^"]+)",document\.baseURI\)\.href/g, (_m, name) => {
  if (!inlined.has(name)) inlined.set(name, dataUrl(name));
  return JSON.stringify(inlined.get(name));
});
const leftover = script.match(
  /["'`]\.?\/?assets\/[A-Za-z0-9_.-]+\.(?:css|png|svg|woff2?|jpg|webp)/g,
);
if (leftover) fail(`asset references left in the script: ${[...new Set(leftover)].join(", ")}`);

// The shell: the stylesheet becomes the same data: URL the script renders (so
// hydration matches), and nothing in it may make the browser fetch the app.
const styles = assets.filter((name) => name.endsWith(".css"));
if (styles.length !== 1) fail(`expected one stylesheet, found ${styles.join(", ")}`);
const styleUrl = inlined.get(styles[0]);
if (!styleUrl) fail(`the script does not reference ${styles[0]}`);
let linked = 0;
html = html.replace(
  /(<link rel="stylesheet" href=")[^"]*assets\/[^"]+\.css(")/g,
  (_m, start, end) => {
    linked += 1;
    return start + styleUrl + end;
  },
);
if (linked !== 1) fail(`expected one stylesheet link in the shell, found ${linked}`);
// The ViVeSec page allows inline styles but not data: stylesheets (style-src
// 'self' 'unsafe-inline'): the same CSS also goes in as a <style> element. React
// keeps the link for hydration and skips the extra element in <head>.
const css = fs.readFileSync(path.join(assetsDir, styles[0]), "utf-8");
if (/<\/style/i.test(css)) fail(`${styles[0]} contains "</style"`);
html = html.replace(
  /<link rel="stylesheet" href="data:text\/css/,
  (link) => `<style>${css}</style>${link}`,
);
const preloadLinks = html.match(/<link rel="modulepreload"[^>]*>/g) ?? [];
html = html.replace(/<link rel="modulepreload"[^>]*>/g, "");
// Route preloads in the dehydrated router state would be rendered as
// <link rel="modulepreload"> again on the client.
html = html.replace(/preloads:(\$R\[\d+\]=)\[[^\]]*\]/g, "preloads:$1[]");
if (/preloads:\$R\[\d+\]=\["/.test(html)) fail("route preloads left in the shell");
// PWA links resolve against the bootloader's location, which ships those files.
html = html.replace(
  /(\s(?:href|src)=")\/(manifest\.webmanifest|favicon\.ico|icons\/|sw\.js)/g,
  "$1./$2",
);
const entries = html.match(/<script type="module"[^>]*src="[^"]+"[^>]*><\/script>/g) ?? [];
if (entries.length !== 1) fail(`expected one module entry in the shell, found ${entries.length}`);

// Ran once the script starts: the bootloader waits for it.
script = `globalThis.matrixAppStarted=true;\n${script}`;

const bundle = Buffer.from(
  JSON.stringify({ format: 1, label: build.label, version: build.version, html, script }),
  "utf-8",
);
const sha256 = crypto.createHash("sha256").update(bundle).digest("hex");
const manifest = {
  format: 1,
  kind: "vivesec-matrix-app",
  label: build.label,
  version: build.version,
  commit: build.commit,
  built_at: build.built_at,
  bundle: { sha256, bytes: bundle.length },
};

fs.rmSync(outDir, { recursive: true, force: true });
fs.mkdirSync(path.join(outDir, "app"), { recursive: true });
fs.writeFileSync(path.join(outDir, "app", "bundle.json"), bundle);
fs.writeFileSync(
  path.join(outDir, "app", "manifest.json"),
  `${JSON.stringify(manifest, null, 2)}\n`,
);

// The bootloader: static files the ViVeSec serves next to their frame socket.
const bootDir = path.join(outDir, "boot");
fs.mkdirSync(bootDir, { recursive: true });
for (const name of fs.readdirSync(bootSource)) {
  fs.copyFileSync(path.join(bootSource, name), path.join(bootDir, name));
}
for (const name of ["favicon.ico", "manifest.webmanifest", "icons"]) {
  fs.cpSync(path.join(clientDir, name), path.join(bootDir, name), { recursive: true });
}
fs.writeFileSync(
  path.join(bootDir, "version.json"),
  `${JSON.stringify({ ...build, kind: "vivesec-matrix-boot" }, null, 2)}\n`,
);

// A stored (uncompressed) zip: no dependency, and the files are small.
function zip(dir, target) {
  const files = [];
  const walk = (base) => {
    for (const name of fs.readdirSync(path.join(dir, base)).sort()) {
      const rel = base ? `${base}/${name}` : name;
      if (fs.statSync(path.join(dir, rel)).isDirectory()) walk(rel);
      else files.push(rel);
    }
  };
  walk("");
  const parts = [];
  const central = [];
  let offset = 0;
  for (const rel of files) {
    const data = fs.readFileSync(path.join(dir, rel));
    const name = Buffer.from(rel, "utf-8");
    const crc = zlib.crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(name.length, 26);
    parts.push(local, name, data);
    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4);
    entry.writeUInt16LE(20, 6);
    entry.writeUInt16LE(0x0800, 8);
    entry.writeUInt32LE(crc, 16);
    entry.writeUInt32LE(data.length, 20);
    entry.writeUInt32LE(data.length, 24);
    entry.writeUInt16LE(name.length, 28);
    entry.writeUInt32LE(offset, 42);
    central.push(entry, name);
    offset += local.length + name.length + data.length;
  }
  const centralSize = central.reduce((sum, part) => sum + part.length, 0);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(offset, 16);
  fs.writeFileSync(target, Buffer.concat([...parts, ...central, end]));
  return files.length;
}
const zipName = `matrix-ai-boot-${build.label.replace(/[^A-Za-z0-9.+-]/g, "_")}.zip`;
const zipped = zip(bootDir, path.join(outDir, zipName));

console.log(
  `[matrix-bundle] ${build.label}: bundle ${(bundle.length / 1048576).toFixed(2)} MB sha256=${sha256}; ` +
    `${inlined.size} asset(s) inlined, ${preloadLinks.length} preload link(s) removed; ` +
    `boot ${zipName} (${zipped} files)`,
);
