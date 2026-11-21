import type { Lang } from "./i18n";

const DATE_LOCALES: Record<Lang, string> = { EN: "en-GB", HU: "hu-HU", DA: "da-DK", DE: "de-DE" };

/** A date and time as the screen language writes it (never the browser's US default). */
export function formatDateTime(at: Date | number | string, lang: Lang): string {
  const date = at instanceof Date ? at : new Date(at);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(DATE_LOCALES[lang] ?? "en-GB", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
