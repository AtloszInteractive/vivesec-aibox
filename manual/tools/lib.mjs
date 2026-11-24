// Shared helpers for the manual screenshot tools.
import { createHmac } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = dirname(fileURLToPath(import.meta.url));
export const BASE_URL = (process.env.MANUAL_BASE_URL || "http://localhost:18080").replace(
  /\/$/,
  "",
);
// Session cookies and TOTP secrets stay outside the repository.
export const STATE_DIR = process.env.MANUAL_STATE_DIR || "";
export const VIEWPORT = { width: 1440, height: 900 };

export function requireStateDir() {
  if (!STATE_DIR) throw new Error("Set MANUAL_STATE_DIR to a folder outside the repository.");
  mkdirSync(STATE_DIR, { recursive: true });
  return STATE_DIR;
}

export function statePath(account) {
  return join(requireStateDir(), `${account}.state.json`);
}

export function secretsPath() {
  return join(requireStateDir(), "secrets.json");
}

export function loadSecrets() {
  const file = secretsPath();
  return existsSync(file) ? JSON.parse(readFileSync(file, "utf8")) : {};
}

export function saveSecret(username, secret) {
  const all = loadSecrets();
  all[username] = secret;
  writeFileSync(secretsPath(), JSON.stringify(all, null, 2));
}

function base32Decode(input) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const clean = input.replace(/[\s=]/g, "").toUpperCase();
  let bits = 0;
  let value = 0;
  const out = [];
  for (const ch of clean) {
    const idx = alphabet.indexOf(ch);
    if (idx < 0) throw new Error("Invalid base32 secret");
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(out);
}

export function totp(secret, now = Date.now()) {
  const counter = Math.floor(now / 1000 / 30);
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(counter));
  const hmac = createHmac("sha1", base32Decode(secret)).update(msg).digest();
  const offset = hmac[hmac.length - 1] & 0x0f;
  const code = (hmac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return String(code).padStart(6, "0");
}

// Codes are single-use: wait for a fresh 30 s window when too close to its end.
export async function freshTotp(secret) {
  const left = 30 - (Math.floor(Date.now() / 1000) % 30);
  if (left < 5) await new Promise((r) => setTimeout(r, (left + 1) * 1000));
  return totp(secret);
}

export async function setHungarian(page) {
  await page.addInitScript(() => {
    try {
      localStorage.setItem("vivesec_lang", "hu");
      localStorage.setItem("vivesec_theme", "light");
    } catch {
      /* storage blocked */
    }
  });
}
