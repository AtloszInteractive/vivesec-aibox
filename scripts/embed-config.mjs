import fs from "node:fs";
import path from "node:path";
import process from "node:process";

// Post-processes the embed build so no mount prefix is baked into the artifact:
// the same files work at any path, resolved from the document URL (or from a
// <base href> the host injects).
//
// This leans on the shape of the generated output, so every step below is
// verified: a framework upgrade that changes that shape must fail the build here
// rather than yield an artifact that only 404s once mounted under a prefix.

const clientDir = path.resolve("dist/client");
const file = path.join(clientDir, "index.html");

const fail = (message) => {
  console.error(`[embed-config] ${message}`);
  process.exit(1);
};

if (!fs.existsSync(file)) fail(`${file} not found — run the embed build first.`);

const html = fs.readFileSync(file, "utf-8");

// TanStack Start overwrites the router basepath during hydration. A pre-transform
// in vite.config.ts turns that inlined constant into a runtime expression; if the
// call disappears or becomes a literal again, the app 404s under every prefix.
const basepathCalls = fs
  .readdirSync(path.join(clientDir, "assets"))
  .filter((name) => name.endsWith(".js"))
  .flatMap(
    (name) =>
      fs
        .readFileSync(path.join(clientDir, "assets", name), "utf-8")
        .match(/update\(\{basepath:.{0,40}/g) ?? [],
  );

if (basepathCalls.length === 0) {
  fail("no hydration basepath assignment found — TanStack Start internals changed.");
}
const literal = basepathCalls.find((call) => /update\(\{basepath:\s*["']/.test(call));
if (literal) fail(`hydration basepath is a build-time constant: ${literal}`);

const patched = html
  // The framework emits the entry script, preloads and PWA links root-absolute
  // from the build-time base; relative they follow wherever the app is mounted.
  .replace(/(\s(?:href|src)=")\/(?!\/)/g, "$1./")
  // Route preload hints live in the serialized router manifest, not in attributes.
  .replace(/"\/assets\//g, '"./assets/');

const leftovers = patched.match(/\s(?:href|src)="\/(?!\/)[^"]*"/g);
if (leftovers) fail(`root-absolute URLs left in index.html: ${leftovers.join(", ")}`);

fs.writeFileSync(file, patched);
console.log(
  `[embed-config] artifact is mount-agnostic; ${basepathCalls.length} runtime basepath assignment(s) verified`,
);
