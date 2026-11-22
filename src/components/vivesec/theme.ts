import { useCallback, useEffect, useSyncExternalStore } from "react";

/* Light / dark theme. The user's choice is kept in this browser only; "system"
   (the default) follows the operating system and switches live with it. The
   `.dark` class on <html> selects the token set in src/styles.css. */

export type ThemePref = "dark" | "light" | "system";

const STORAGE_KEY = "vivesec_theme";
const QUERY = "(prefers-color-scheme: dark)";
/** Browser chrome colour per theme: the header background. */
const THEME_COLOR = { dark: "#1B272D", light: "#FFFFFF" } as const;

/** Runs in <head> before the first paint, so the theme never flashes.
 *  Unknown stored values count as "system"; no matchMedia means dark. */
export const THEME_INIT_SCRIPT = `(function(){try{var p=localStorage.getItem('${STORAGE_KEY}');var d=p==='dark'||(p!=='light'&&(!window.matchMedia||window.matchMedia('${QUERY}').matches));document.documentElement.classList.toggle('dark',d);var m=document.querySelector('meta[name=theme-color]');if(m)m.setAttribute('content',d?'${THEME_COLOR.dark}':'${THEME_COLOR.light}')}catch(e){}})()`;

function readPref(): ThemePref {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    return saved === "dark" || saved === "light" ? saved : "system";
  } catch {
    return "system";
  }
}

function systemDark(): boolean {
  return typeof window.matchMedia !== "function" || window.matchMedia(QUERY).matches;
}

function apply(pref: ThemePref) {
  const dark = pref === "dark" || (pref === "system" && systemDark());
  document.documentElement.classList.toggle("dark", dark);
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", dark ? THEME_COLOR.dark : THEME_COLOR.light);
}

const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  const onStorage = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY) {
      apply(readPref());
      listener();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/** The stored theme choice and its setter; the page follows it immediately. */
export function useTheme(): [ThemePref, (pref: ThemePref) => void] {
  const pref = useSyncExternalStore(subscribe, readPref, () => "system" as ThemePref);

  useEffect(() => {
    apply(pref);
    if (pref !== "system" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(QUERY);
    const onChange = () => apply("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [pref]);

  const setPref = useCallback((next: ThemePref) => {
    try {
      if (next === "system") window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage may be blocked (private mode, embedding): the choice then lasts for this page.
    }
    apply(next);
    listeners.forEach((l) => l());
  }, []);

  return [pref, setPref];
}
