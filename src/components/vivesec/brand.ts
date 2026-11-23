// ViVeSec brand colours (brandbook 1.1, logo kit 2026-10-24) for the dark and the
// light theme. The theme-dependent values are CSS variables from src/styles.css,
// so inline styles follow the active theme without a re-render.
// The brand purple fails the 4.5:1 text contrast on the dark surfaces, so text
// and icons use a tint of it (lighter on graphite, darker on white); a purple fill
// always carries white text.
// Imported as assets so both the SSR and the mount-agnostic embed build resolve them.
import logoOnDark from "@/assets/brand/vivesec-logo-purple-white.png";
import logoOnLight from "@/assets/brand/vivesec-logo-purple-black.png";

/** ViVeSec purple: fills, borders, tints and status accents (the same in both themes). */
export const ACCENT = "#8C49E9";
/** Purple tint for text and icons: #B48CF5 on graphite, #7A3AD6 on white (at least 4.5:1). */
export const ACCENT_TEXT = "var(--brand-soft)";
/** Text and icons on an ACCENT fill (4.9:1). */
export const ON_ACCENT = "#FFFFFF";
/** Error and offline text: rose-400 on graphite, rose-700 on white. */
export const DANGER_TEXT = "var(--danger-text)";

/** The foreground colour at an opacity, for inline styles (the `ink/NN` class equivalent). */
export function inkA(alpha: number): string {
  return `color-mix(in srgb, var(--ink) ${Math.round(alpha * 1000) / 10}%, transparent)`;
}

/** Like inkA, for text: the light theme lifts low opacities to stay readable,
 *  exactly as the `text-ink/NN` classes do in src/styles.css. */
export function inkText(alpha: number): string {
  const a = Math.round(alpha * 100);
  const lifted = a >= 90 ? 95 : Math.round(60 + (40 * (a - 25)) / 75);
  return `color-mix(in oklab, var(--ink) calc(${a}% + var(--ink-lift) * ${Math.max(0, lifted - a)}%), transparent)`;
}

/** The full logo for dark surfaces (kit file Lila_Feher_Atlatszo, 512 px wide). */
export const LOGO_ON_DARK: string = logoOnDark;
/** The full logo for light surfaces (kit file Lila_Fekete_Atlatszo, 512 px wide). */
export const LOGO_ON_LIGHT: string = logoOnLight;
/** File width / visible logo width (the PNG carries its mandatory clear space). */
export const LOGO_FILE_RATIO = 1 / 0.903;
/** File height / file width of the full logo PNGs. */
export const LOGO_ASPECT = 148 / 512;

/** The product's name inside the ViVeSec app, where its logo would show twice
 *  (the Matrix build, `npm run build:matrix`). A working name for now. */
export const MATRIX_NAME = "VIVEMATRIX";
