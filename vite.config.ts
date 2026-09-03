// @lovable.dev/vite-tanstack-config already includes the following — do NOT add them manually
// or the app will break with duplicate plugins:
//   - tanstackStart, viteReact, tailwindcss, tsConfigPaths, nitro (build-only using cloudflare as a default target),
//     componentTagger (dev-only), VITE_* env injection, @ path alias, React/TanStack dedupe,
//     error logger plugins, and sandbox detection (port/host/strictPort).
// You can pass additional config via defineConfig({ vite: { ... }, etc... }) if needed.
import { defineConfig } from "@lovable.dev/vite-tanstack-config";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

// Vite only injects VITE_* into the client bundle, so server-only settings
// (ADAPTER_URL & co.) would be missing whenever `npm run dev` runs without an
// exported shell env. Load them here; a real environment variable always wins.
for (const file of [".env", ".env.local"]) {
  const filePath = path.resolve(process.cwd(), file);
  if (!fs.existsSync(filePath)) continue;
  for (const line of fs.readFileSync(filePath, "utf-8").split("\n")) {
    const match = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line);
    if (!match || process.env[match[1]] !== undefined) continue;
    let value = match[2].trim().replace(/\s+#.*$/, "");
    if (/^".*"$|^'.*'$/.test(value)) value = value.slice(1, -1);
    process.env[match[1]] = value;
  }
}

// ViVeSec embeds the UI as a static SPA (one index.html they patch with their
// frame-socket transport), while the box itself keeps serving SSR.
// `--mode embed` is used instead of an env var so the script stays cross-platform.
const modeIndex = process.argv.indexOf("--mode");
const buildMode = modeIndex >= 0 ? process.argv[modeIndex + 1] : "";
const EMBED_SPA = process.env.VIVESEC_SPA === "1" || buildMode === "embed";
if (EMBED_SPA) process.env.NODE_ENV ||= "production";

const EMBED_BASE = process.env.VIVESEC_BASE ?? "";

// TanStack Start overwrites the router basepath during hydration with a constant
// it inlines at build time, which would discard whatever the app resolved at
// runtime. Replacing the constant with an expression keeps the mount prefix a
// deploy-time value. Falls back to "/" while prerendering, where there is no DOM.
const RUNTIME_BASEPATH =
  '(typeof document!=="undefined"?new URL(document.baseURI).pathname.replace(/\\/+$/,"")||"/":"/")';

// Start's own define replaces this constant, so the substitution has to happen
// before it runs — a plain `define` in this config loses to the plugin's.
const runtimeRouterBasepath = {
  name: "vivesec-runtime-router-basepath",
  enforce: "pre" as const,
  transform(code: string, id: string) {
    if (!id.includes("hydrateStart") || !code.includes("TSS_ROUTER_BASEPATH")) return null;
    return {
      code: code.replaceAll("process.env.TSS_ROUTER_BASEPATH", RUNTIME_BASEPATH),
      map: null,
    };
  },
};

// ViVeSec serves one artifact under several mount prefixes (/latest/, /<version>/),
// so asset URLs are resolved at runtime against the <base href> they inject rather
// than baked in. Anything not reached from JS is emitted relative, which the same
// <base href> resolves.
const portableAssetUrls = {
  renderBuiltUrl(
    filename: string,
    { hostType, ssr }: { hostType: "js" | "css" | "html"; ssr?: boolean },
  ) {
    // Prerendering runs in Node, so this value is baked into the shipped HTML —
    // it has to be a plain relative URL the <base> element can resolve.
    if (ssr) return { runtime: JSON.stringify(`./${filename}`) };
    if (hostType !== "js") return { relative: true };
    return { runtime: `new URL(${JSON.stringify(filename)},document.baseURI).href` };
  },
};

export default defineConfig({
  // A pinned prefix is the fallback for hosts that cannot inject <base href>: it is
  // baked into every asset URL, so the artifact then only works under that prefix.
  // It must be absolute — TanStack Router prepends its own "/" to a relative base
  // and emits broken "/./assets/..." URLs.
  ...(EMBED_SPA
    ? {
        vite: EMBED_BASE
          ? { base: EMBED_BASE }
          : {
              experimental: portableAssetUrls,
              plugins: [runtimeRouterBasepath],
            },
      }
    : {}),
  // Build a Node server bundle (.output/server/index.mjs) so the app can run in a
  // container on Google Cloud Run. The Node preset honours the PORT env var that
  // Cloud Run injects (defaults to 8080).
  // The embed build skips nitro: its SPA prerender boots a preview server from
  // dist/server/server.js, which the nitro output layout does not produce.
  nitro: EMBED_SPA ? false : { preset: "node-server" },
  tanstackStart: {
    // Redirect TanStack Start's bundled server entry to src/server.ts (our SSR error wrapper).
    // nitro/vite builds from this
    server: { entry: "server" },
    // ViVeSec patches a single index.html entry point, so emit the SPA shell
    // under that name instead of the default _shell.html.
    ...(EMBED_SPA ? { spa: { enabled: true, prerender: { outputPath: "index.html" } } } : {}),
  },
});
