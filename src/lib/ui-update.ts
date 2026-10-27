// Stale-tab detection for the box-served (SSR) UI. A release swaps the bundle
// under an open tab, which keeps running the old code until it reloads, so the
// tab asks the server which bundle it would serve now (version.json, written
// by vite.config.ts) and offers a reload when that differs from its own build.
// Not used under the ViVeSec embedding: the host owns that bundle's updates.
import { useCallback, useEffect, useRef, useState } from "react";

import { UI_BUILD } from "@/lib/build-info";
import { isEmbedded } from "@/lib/rag/adapter-client";

const CHECK_INTERVAL_MS = 5 * 60_000;
const DRAFT_KEY = "vivesec.reloadDraft";

/** Label of the bundle the server serves now; null when it cannot be told. */
export async function servedUiLabel(): Promise<string | null> {
  try {
    const response = await fetch(`${import.meta.env.BASE_URL}version.json`, { cache: "no-store" });
    if (!response.ok) return null;
    const body = (await response.json()) as { label?: unknown };
    return typeof body.label === "string" && body.label ? body.label : null;
  } catch {
    return null;
  }
}

/**
 * True once the server serves a different UI build than this tab runs.
 * `recheckKey` forces a check when it changes (e.g. the box came back online
 * after a restart); visibility, focus and a slow timer trigger the rest.
 */
export function useUiUpdateAvailable(recheckKey?: unknown): boolean {
  const [available, setAvailable] = useState(false);
  const inFlight = useRef(false);

  const check = useCallback(async () => {
    if (inFlight.current || isEmbedded() || document.visibilityState === "hidden") return;
    inFlight.current = true;
    try {
      const served = await servedUiLabel();
      if (served && served !== UI_BUILD.label) setAvailable(true);
    } finally {
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    void check();
  }, [check, recheckKey]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void check();
    };
    const id = window.setInterval(() => void check(), CHECK_INTERVAL_MS);
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [check]);

  return available;
}

/** Reloads the page, carrying an unsent draft over to the new bundle. */
export function reloadKeepingDraft(draft: string): void {
  try {
    if (draft.trim()) sessionStorage.setItem(DRAFT_KEY, draft);
  } catch {
    // Storage may be unavailable; the reload still has to happen.
  }
  window.location.reload();
}

/** The draft saved by reloadKeepingDraft, removed once read. */
export function takeReloadDraft(): string {
  try {
    const draft = sessionStorage.getItem(DRAFT_KEY) ?? "";
    sessionStorage.removeItem(DRAFT_KEY);
    return draft;
  } catch {
    return "";
  }
}
