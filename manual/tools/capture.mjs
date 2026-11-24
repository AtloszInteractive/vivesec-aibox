// Captures the screenshots of the Hungarian user manual from a running Matrix AI.
// Run login.mjs first; it stores the sessions this script uses.
//   node capture.mjs            all screenshots
//   node capture.mjs graf admin only the screenshots whose name contains a filter
import { chromium } from "playwright";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { BASE_URL, ROOT, VIEWPORT, setHungarian, statePath } from "./lib.mjs";

const IMG = join(ROOT, "..", "hu", "img");
const CASE_REF = process.env.MANUAL_CASE_REF || "2026/1124/0001";
const PERSON = process.env.MANUAL_PERSON || "Kovács Péter";
const PERSON_ID = process.env.MANUAL_PERSON_ID || "NY-284486";
const QUESTION =
  process.env.MANUAL_QUESTION ||
  `Kik ${PERSON} (${PERSON_ID}) kapcsolatai, és milyen dokumentumok említik?`;
const ANSWER_TIMEOUT = Number(process.env.MANUAL_ANSWER_TIMEOUT_MS || 300000);
const filters = process.argv.slice(2);
const wanted = (name) => !filters.length || filters.some((f) => name.includes(f));

const popup = (page) =>
  page.locator('[role="menu"], [role="dialog"], [data-radix-popper-content-wrapper]').first();

async function shot(page, name, options = {}) {
  await page.waitForTimeout(options.settle ?? 600);
  const target = options.locator ?? page;
  await target.screenshot({
    path: join(IMG, name),
    // The 3D graph renders continuously; freezing animations would never settle.
    animations: options.animations ?? "disabled",
    mask: options.mask,
  });
  console.log("  saved", name);
}

async function openApp(page) {
  await page.goto(`${BASE_URL}/`);
  await page.getByRole("textbox", { name: /üzenet/ }).waitFor({ timeout: 30000 });
  await page.waitForTimeout(1500);
}

async function closePopups(page) {
  await page.keyboard.press("Escape");
  await page.mouse.click(5, VIEWPORT.height - 5);
  await page.waitForTimeout(300);
}

async function setPurpose(page, screenshot) {
  await page.getByRole("button", { name: "Cél megadása" }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.locator("input").first().fill(CASE_REF);
  if (screenshot) await shot(page, screenshot);
  await dialog.getByRole("button", { name: "Mentés" }).click();
  await page.waitForTimeout(800);
}

async function newConversation(page) {
  await page.getByRole("button", { name: "Új beszélgetés" }).click();
  await page.waitForTimeout(1000);
}

async function ask(page, question) {
  const box = page.getByRole("textbox", { name: /üzenet/ });
  await box.fill(question);
  await box.press("Enter");
  // The rating buttons appear once the answer is complete.
  await page.getByRole("button", { name: "Jó válasz" }).last().waitFor({ timeout: ANSWER_TIMEOUT });
  await page.waitForTimeout(1500);
}

const userSteps = [
  {
    names: ["fo-01-kezdokepernyo.png", "fo-02-cel-megadasa.png"],
    run: async (page) => {
      await openApp(page);
      if (await page.getByRole("button", { name: "Új beszélgetés" }).count())
        await newConversation(page);
      // A new tab has no purpose yet (it lives in sessionStorage).
      await shot(page, "fo-01-kezdokepernyo.png");
      await setPurpose(page, "fo-02-cel-megadasa.png");
    },
  },
  {
    names: ["fo-03-fiokmenu.png"],
    run: async (page) => {
      await openApp(page);
      await page.getByRole("button", { name: "Kézikönyv Elemző" }).click();
      await shot(page, "fo-03-fiokmenu.png");
      await closePopups(page);
    },
  },
  {
    names: ["fo-04-beallitasok.png"],
    run: async (page) => {
      await openApp(page);
      await page.getByRole("button", { name: "Beállítások" }).click();
      await shot(page, "fo-04-beallitasok.png");
      await closePopups(page);
    },
  },
  {
    names: [
      "valasz-01-valasz.png",
      "valasz-02-forrasok.png",
      "valasz-03-ertekeles.png",
      "valasz-04-forrasnezo.png",
    ],
    run: async (page) => {
      await openApp(page);
      if (await page.getByRole("button", { name: "Cél megadása" }).count()) await setPurpose(page);
      await newConversation(page);
      await ask(page, QUESTION);
      const card = page.getByRole("button", { name: "Jó válasz" }).last();
      // Top of the answer.
      await page.getByText(QUESTION).first().scrollIntoViewIfNeeded();
      await shot(page, "valasz-01-valasz.png");
      // Sources, actions and rating at the bottom of the card.
      await card.scrollIntoViewIfNeeded();
      await page.mouse.wheel(0, 400);
      await shot(page, "valasz-02-forrasok.png");
      await page.getByRole("button", { name: "Rossz válasz" }).last().click();
      await page.getByRole("button", { name: "Rossz forrás" }).last().waitFor();
      await page.mouse.wheel(0, 400);
      await shot(page, "valasz-03-ertekeles.png");
      // The first citation may be a graph summary; open a real file instead.
      await page
        .getByRole("button", { name: /Összes találat mutatása/ })
        .last()
        .click();
      await page
        .getByRole("button", { name: /^\[#\d+\] \S+\.(pdf|docx|csv|xlsx|txt)\b/ })
        .first()
        .click();
      const passages = page.getByRole("button", { name: /Visszakeresett részletek/ });
      await passages.waitFor({ timeout: 20000 });
      await passages.click();
      await shot(page, "valasz-04-forrasnezo.png", { settle: 1500 });
    },
  },
  {
    names: ["fo-05-beszelgetesek.png"],
    run: async (page) => {
      await openApp(page);
      const toolbar = page.getByRole("button", { name: "Új beszélgetés" }).locator("xpath=..");
      await toolbar.getByRole("button").nth(-2).click();
      await shot(page, "fo-05-beszelgetesek.png");
      await closePopups(page);
    },
  },
  {
    names: ["dok-01-dokumentumok.png"],
    run: async (page) => {
      await openApp(page);
      await page.getByRole("button", { name: "Dokumentumok" }).click();
      await page.getByRole("dialog").waitFor();
      await shot(page, "dok-01-dokumentumok.png", { settle: 2500 });
      await closePopups(page);
    },
  },
  {
    names: [
      "graf-01-kereses.png",
      "graf-02-abra.png",
      "graf-03-feldolgozas.png",
      "graf-04-profil.png",
      "graf-05-naplo.png",
    ],
    run: async (page) => {
      await openApp(page);
      if (await page.getByRole("button", { name: "Cél megadása" }).count()) await setPurpose(page);
      await page.getByRole("button", { name: "Kapcsolatok" }).click();
      const search = page.getByPlaceholder(/Személyek, szervezetek/);
      await search.waitFor();
      await search.fill(PERSON);
      await page.getByRole("button", { name: "Keresés", exact: true }).click();
      const hit = page.getByText(`Nyilvántartási szám: ${PERSON_ID}`).first();
      await hit.waitFor({ timeout: 30000 });
      await shot(page, "graf-01-kereses.png");
      await hit.click();
      await page.waitForTimeout(6000);
      await shot(page, "graf-02-abra.png", { settle: 2000, animations: "allow" });
      for (const [tab, name] of [
        ["Feldolgozás", "graf-03-feldolgozas.png"],
        ["Szakterületi profil", "graf-04-profil.png"],
        ["Lekérdezési napló", "graf-05-naplo.png"],
      ]) {
        await page.getByRole("button", { name: tab, exact: true }).click();
        await shot(page, name, { settle: 2500, animations: "allow" });
      }
    },
  },
];

const ADMIN_TABS = [
  ["Áttekintés", "admin-01-attekintes.png"],
  ["Felhasználók", "admin-02-felhasznalok.png"],
  ["Szobák és jogosultságok", "admin-04-szobak.png"],
  ["AD-csoportok", "admin-05-ad-csoportok.png"],
  ["Cégprofil", "admin-06-cegprofil.png"],
  ["Sablonok", "admin-07-sablonok.png"],
  ["Eseménynapló", "admin-08-esemenynaplo.png"],
  ["Frissítések", "admin-09-frissitesek.png"],
];

const adminSteps = [
  {
    names: [...ADMIN_TABS.map(([, n]) => n), "admin-03-uj-felhasznalo.png"],
    run: async (page) => {
      await page.goto(`${BASE_URL}/admin`);
      const nav = page.getByRole("navigation", { name: "Adminisztráció" });
      await nav.waitFor({ timeout: 30000 });
      for (const [tab, name] of ADMIN_TABS) {
        if (!wanted(name) && !(tab === "Felhasználók" && wanted("admin-03"))) continue;
        await nav.getByRole("button", { name: tab, exact: true }).click();
        await page.waitForTimeout(1800);
        if (tab === "Felhasználók") {
          await page.getByRole("textbox", { name: "Szűrés" }).fill("kezikonyv");
          if (wanted(name)) await shot(page, name);
          if (wanted("admin-03-uj-felhasznalo.png")) {
            await page.getByRole("button", { name: "Új felhasználó" }).click();
            await page.getByRole("dialog").waitFor();
            await shot(page, "admin-03-uj-felhasznalo.png");
            await page.getByRole("dialog").getByRole("button", { name: "Mégse" }).click();
          }
          continue;
        }
        await shot(page, name);
      }
    },
  },
];

async function runSteps(browser, account, steps) {
  const selected = steps.filter((s) => s.names.some(wanted));
  if (!selected.length) return;
  const state = statePath(account);
  if (!existsSync(state)) throw new Error(`No session for ${account}: run login.mjs first`);
  const context = await browser.newContext({
    viewport: VIEWPORT,
    locale: "hu-HU",
    storageState: state,
  });
  const page = await context.newPage();
  await setHungarian(page);
  for (const step of selected) {
    console.log(`${account}: ${step.names.join(", ")}`);
    try {
      await step.run(page);
    } catch (error) {
      console.error(`  FAILED: ${error.message.split("\n")[0]}`);
      await page.screenshot({ path: join(IMG, `_failed-${step.names[0]}`) }).catch(() => {});
    }
  }
  await context.storageState({ path: state });
  await context.close();
}

const browser = await chromium.launch({ args: ["--lang=hu-HU"] });
try {
  await runSteps(browser, "user", userSteps);
  await runSteps(browser, "admin", adminSteps);
} finally {
  await browser.close();
}
