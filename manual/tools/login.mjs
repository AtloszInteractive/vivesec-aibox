// Signs the manual accounts in and stores their sessions outside the repository.
// First sign-in of a fresh account: registers the authenticator (the secret is kept
// in MANUAL_STATE_DIR) and sets the new password. With MANUAL_SHOTS=1 it also
// captures the sign-in screens of the analyst account.
import { chromium } from "playwright";
import { join } from "node:path";
import {
  BASE_URL,
  ROOT,
  VIEWPORT,
  freshTotp,
  loadSecrets,
  saveSecret,
  setHungarian,
  statePath,
} from "./lib.mjs";

const IMG = join(ROOT, "..", "hu", "img");
const SHOTS = process.env.MANUAL_SHOTS === "1";

const accounts = [
  {
    key: "user",
    username: process.env.MANUAL_USER,
    password: process.env.MANUAL_PASS,
    newPassword: process.env.MANUAL_NEW_PASS,
    shots: SHOTS,
  },
  {
    key: "admin",
    username: process.env.MANUAL_ADMIN_USER,
    password: process.env.MANUAL_ADMIN_PASS,
    newPassword: process.env.MANUAL_ADMIN_NEW_PASS,
    shots: false,
  },
].filter((a) => a.username);

async function shot(page, name, mask = []) {
  await page.waitForTimeout(400);
  await page.screenshot({ path: join(IMG, name), mask, maskColor: "#8C49E9" });
  console.log("  saved", name);
}

async function signIn(browser, account) {
  const context = await browser.newContext({ viewport: VIEWPORT, locale: "hu-HU" });
  const page = await context.newPage();
  await setHungarian(page);
  await page.goto(`${BASE_URL}/login`);
  const form = page.locator("form");
  await form.locator('input[name="username"]').fill(account.username);
  if (account.shots) await shot(page, "belepes-01-jelszo.png");
  // Each attempt below uses the password that is still valid on the box.
  let password = account.password;
  const secrets = loadSecrets();
  if (secrets[account.username]?.password) password = secrets[account.username].password;
  await form.locator('input[name="password"]').fill(password);
  await form.locator('button[type="submit"]').click();

  const code = page.locator('input[name="code"]');
  try {
    await code.waitFor({ timeout: 20000 });
  } catch {
    const message = await page
      .locator("form")
      .innerText()
      .catch(() => "");
    throw new Error(
      `${account.username}: no code prompt after the password. Page says: ${message}`,
    );
  }
  const secretBox = page.locator("p.select-all");
  let secret = secrets[account.username]?.totp;
  if (await secretBox.count()) {
    secret = (await secretBox.innerText()).replace(/\s+/g, "");
    saveSecret(account.username, { totp: secret, password });
    if (account.shots) {
      await shot(page, "belepes-02-ketlepcsos-beallitas.png", [
        secretBox,
        page.locator('form svg[role="img"]'),
      ]);
    }
  } else if (account.shots) {
    await shot(page, "belepes-03-ellenorzo-kod.png");
  }
  if (!secret) throw new Error(`No TOTP secret known for ${account.username}`);
  await code.fill(await freshTotp(secret));
  await page.locator('form button[type="submit"]').click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });

  if (new URL(page.url()).pathname.startsWith("/password")) {
    if (!account.newPassword) throw new Error(`Set the new password for ${account.username}`);
    await page.locator('input[type="password"]').first().waitFor();
    if (account.shots) await shot(page, "belepes-04-jelszocsere.png");
    const fields = page.locator('form input[type="password"]');
    await fields.nth(0).fill(password);
    await fields.nth(1).fill(account.newPassword);
    await fields.nth(2).fill(account.newPassword);
    await page.locator('form button[type="submit"]').click();
    await page.waitForURL((u) => !u.pathname.startsWith("/password"), { timeout: 20000 });
    saveSecret(account.username, { totp: secret, password: account.newPassword });
  }
  await page.waitForTimeout(1500);
  await context.storageState({ path: statePath(account.key) });
  console.log(`${account.username}: signed in`);
  await context.close();
}

const browser = await chromium.launch({ args: ["--lang=hu-HU"] });
try {
  for (const account of accounts) await signIn(browser, account);
} finally {
  await browser.close();
}
