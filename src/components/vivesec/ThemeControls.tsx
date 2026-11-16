import { Monitor, Moon, Sun } from "lucide-react";

import { LOGO_ASPECT, LOGO_FILE_RATIO, LOGO_ON_DARK, LOGO_ON_LIGHT } from "./brand";
import { useLang } from "./i18n";
import { useTheme, type ThemePref } from "./theme";

/** The full ViVeSec logo as delivered, in the variant of the active theme
 *  (Lila_Fekete on light, Lila_Feher on dark). The PNG carries its clear space,
 *  so `visibleWidth` is the logo itself; the guide's digital minimum is 160 px. */
export function ThemeLogo({
  visibleWidth,
  className = "",
}: {
  visibleWidth: number;
  className?: string;
}) {
  const width = Math.round(visibleWidth * LOGO_FILE_RATIO);
  const height = Math.round(width * LOGO_ASPECT);
  const common = { alt: "ViVeSec", width, height, draggable: false, style: { width, height } };
  // Both variants are in the markup and CSS shows one, so the server-rendered
  // page is right before hydration and a theme switch needs no re-render.
  return (
    <>
      <img
        src={LOGO_ON_LIGHT}
        {...common}
        className={`shrink-0 select-none dark:hidden ${className}`}
      />
      <img
        src={LOGO_ON_DARK}
        {...common}
        className={`hidden shrink-0 select-none dark:block ${className}`}
      />
    </>
  );
}

const OPTIONS: { value: ThemePref; label: string; Icon: typeof Sun }[] = [
  { value: "dark", label: "Dark", Icon: Moon },
  { value: "light", label: "Light", Icon: Sun },
  { value: "system", label: "System", Icon: Monitor },
];

/** Dark / Light / System switch for the page headers. `className` lets a crowded
 *  header hide it on narrow screens (ThemeMenuItems then carries the choice). */
export function ThemeToggle({ className = "inline-flex" }: { className?: string }) {
  const { tm } = useLang();
  const [pref, setPref] = useTheme();
  return (
    <div
      role="radiogroup"
      aria-label={tm("Theme")}
      className={`${className} shrink-0 items-center gap-0.5 rounded-full border border-ink/10 bg-ink/5 p-0.5`}
    >
      {OPTIONS.map(({ value, label, Icon }) => {
        const active = pref === value;
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={tm(label)}
            title={`${tm("Theme")}: ${tm(label)}`}
            onClick={() => setPref(value)}
            className={`grid h-6 w-6 place-items-center rounded-full transition ${
              active ? "bg-ink/15 text-brand-soft" : "text-ink/60 hover:bg-ink/10 hover:text-ink"
            }`}
          >
            <Icon className="h-3.5 w-3.5" />
          </button>
        );
      })}
    </div>
  );
}

/** The same choice as menu rows, for a dropdown on narrow screens. */
export function ThemeMenuItems({ className = "" }: { className?: string }) {
  const { tm } = useLang();
  const [pref, setPref] = useTheme();
  return (
    <div
      role="radiogroup"
      aria-label={tm("Theme")}
      className={`border-t border-ink/10 py-1 ${className}`}
    >
      <div className="px-3 pb-1 pt-1.5 text-[10.5px] uppercase tracking-wider text-ink/55">
        {tm("Theme")}
      </div>
      {OPTIONS.map(({ value, label, Icon }) => {
        const active = pref === value;
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => setPref(value)}
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] text-ink/80 transition hover:bg-ink/5"
          >
            <Icon className={`h-3.5 w-3.5 ${active ? "text-brand-soft" : "text-ink/60"}`} />
            <span className={`flex-1 ${active ? "text-brand-soft" : ""}`}>{tm(label)}</span>
          </button>
        );
      })}
    </div>
  );
}
