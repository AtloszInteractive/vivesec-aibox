// ViVeSec brand colours for the dark interface (brandbook 1.1, logo kit 2026-10-24).
// The brand purple fails the 4.5:1 text contrast on the navy surfaces, so text
// and icons use a lighter tint of it; a purple fill always carries white text.
// Imported as an asset so both the SSR and the mount-agnostic embed build resolve it.
import logoOnDark from "@/assets/brand/vivesec-logo-purple-white.png";

/** ViVeSec purple: fills, borders, tints and status accents. */
export const ACCENT = "#8C49E9";
/** Purple tint for text and icons on the navy surfaces (at least 4.5:1 on all of them). */
export const ACCENT_TEXT = "#B48CF5";
/** Text and icons on an ACCENT fill (4.9:1). */
export const ON_ACCENT = "#FFFFFF";
/** Deep navy: the header and the darkest surfaces (the logo's own background). */
export const NAVY = "#071625";

/** The full logo for dark surfaces (kit file Lila_Feher_Atlatszo, 512 px wide). */
export const LOGO_ON_DARK: string = logoOnDark;
/** File width / visible logo width (the PNG carries its mandatory clear space). */
export const LOGO_FILE_RATIO = 1 / 0.903;
/** File height / file width of the full logo PNGs. */
export const LOGO_ASPECT = 148 / 512;
