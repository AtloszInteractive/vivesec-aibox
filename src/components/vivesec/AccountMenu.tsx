import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, KeyRound, LogOut, Settings2, UserRound } from "lucide-react";

import { readAdapterSystem, readAuthSystem } from "@/lib/auth/admin-client";
import { fetchGateSession, gateLogout, gatePath } from "@/lib/auth/client";
import {
  ADMIN_PATH,
  ADMIN_ROLE,
  LOGIN_PATH,
  PASSWORD_PATH,
  type GateUser,
} from "@/lib/auth/protocol";
import { adapterSession, isEmbedded, isMatrixEmbed } from "@/lib/rag/adapter-client";
import { EmbeddedAdmin } from "./admin/AdminPage";
import { needsAttention } from "./admin/status";
import { ACCENT_TEXT } from "./brand";
import { useLang } from "./i18n";

/**
 * The signed-in user of a standalone box, with password change and sign-out.
 * Renders nothing on a box in vivesecbox mode or in the ViVeSecBox embedding,
 * where the ViVeSecBox owns the login, with one exception: inside the Matrix
 * app an administrator gets the administration page, opened over the app.
 */
export function AccountMenu() {
  const { tm } = useLang();
  const [user, setUser] = useState<GateUser | null>(null);
  const [embedded, setEmbedded] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
  const [open, setOpen] = useState(false);
  const [attention, setAttention] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const checkAttention = async () => {
      // The clock and the virus definitions, so an administrator notices a
      // problem without opening the administration page.
      const [auth, adapter] = await Promise.all([
        readAuthSystem().catch(() => null),
        readAdapterSystem().catch(() => null),
      ]);
      if (!cancelled) setAttention(needsAttention(auth, adapter));
    };
    if (isEmbedded()) {
      if (!isMatrixEmbed()) return;
      void adapterSession()
        .then(async (s) => {
          if (cancelled || !s.roles.includes(ADMIN_ROLE)) return;
          setEmbedded(true);
          setUser({
            username: s.user,
            display_name: s.name,
            source: "local",
            roles: s.roles,
            drives: [],
          });
          await checkAttention();
        })
        .catch(() => undefined);
      return () => {
        cancelled = true;
      };
    }
    void fetchGateSession().then(async (s) => {
      if (cancelled || !s.ok || !s.standalone || !s.authenticated) return;
      setUser(s.user);
      if (!s.user.roles.includes(ADMIN_ROLE)) return;
      await checkAttention();
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  if (!user) return null;
  const name = user.display_name || user.username;

  async function signOut() {
    await gateLogout();
    window.location.assign(gatePath(LOGIN_PATH));
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title={tm("Account")}
        className="relative inline-flex max-w-[12rem] items-center gap-1.5 rounded-full border border-ink/10 bg-ink/5 px-2.5 py-1.5 text-xs text-ink/85 transition hover:bg-ink/10"
      >
        <UserRound className="h-3.5 w-3.5 shrink-0" style={{ color: ACCENT_TEXT }} />
        <span className="hidden truncate sm:inline">{name}</span>
        <ChevronDown
          className={`h-3.5 w-3.5 shrink-0 text-ink/50 transition-transform ${open ? "rotate-180" : ""}`}
        />
        {attention && (
          <span
            className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-amber-400"
            title={tm("Needs attention")}
          />
        )}
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-50 mt-2 w-64 overflow-hidden rounded-xl border border-ink/10 bg-surface-3 py-1 shadow-2xl shadow-shade/60"
        >
          <div className="border-b border-ink/5 px-3 py-2">
            <div className="truncate text-[13px] text-ink">{name}</div>
            {user.display_name && (
              <div className="truncate text-[11px] text-ink/50">{user.username}</div>
            )}
            {!embedded && user.drives.length === 0 && (
              <p className="mt-1.5 text-[11px] text-amber-800/90 dark:text-amber-100/90">
                {tm("No rooms are assigned to you yet. Ask the administrator for access.")}
              </p>
            )}
          </div>
          {user.roles.includes(ADMIN_ROLE) &&
            (embedded ? (
              <button
                role="menuitem"
                type="button"
                onClick={() => {
                  setOpen(false);
                  setAdminOpen(true);
                }}
                className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-[13px] text-ink/80 transition hover:bg-ink/5 hover:text-brand-soft"
              >
                <Settings2 className="h-3.5 w-3.5" />
                {tm("Administration")}
                {attention && (
                  <span className="ml-auto text-[11px] text-amber-800 dark:text-amber-200">
                    {tm("Needs attention")}
                  </span>
                )}
              </button>
            ) : (
              <a
                role="menuitem"
                href={gatePath(ADMIN_PATH)}
                className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-[13px] text-ink/80 transition hover:bg-ink/5 hover:text-brand-soft"
              >
                <Settings2 className="h-3.5 w-3.5" />
                {tm("Administration")}
                {attention && (
                  <span className="ml-auto text-[11px] text-amber-800 dark:text-amber-200">
                    {tm("Needs attention")}
                  </span>
                )}
              </a>
            ))}
          {!embedded && user.source !== "ldap" && (
            <a
              role="menuitem"
              href={gatePath(PASSWORD_PATH)}
              className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-[13px] text-ink/80 transition hover:bg-ink/5 hover:text-brand-soft"
            >
              <KeyRound className="h-3.5 w-3.5" />
              {tm("Change password")}
            </a>
          )}
          {!embedded && (
            <button
              role="menuitem"
              type="button"
              onClick={() => void signOut()}
              className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-[13px] text-ink/80 transition hover:bg-ink/5 hover:text-brand-soft"
            >
              <LogOut className="h-3.5 w-3.5" />
              {tm("Sign out")}
            </button>
          )}
        </div>
      )}
      {adminOpen &&
        createPortal(
          <div
            className="fixed inset-0 z-[100] overflow-y-auto"
            style={{ backgroundColor: "var(--surface-0)" }}
          >
            <EmbeddedAdmin onClose={() => setAdminOpen(false)} />
          </div>,
          document.body,
        )}
    </div>
  );
}
