import { useEffect, useRef, useState } from "react";
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
import { isEmbedded } from "@/lib/rag/adapter-client";
import { needsAttention } from "./admin/status";
import { ACCENT_TEXT } from "./brand";
import { useLang } from "./i18n";

/**
 * The signed-in user of a standalone box, with password change and sign-out.
 * Renders nothing in the ViVeSecBox embedding or on a box in vivesecbox mode,
 * where the ViVeSecBox owns the login.
 */
export function AccountMenu() {
  const { tm } = useLang();
  const [user, setUser] = useState<GateUser | null>(null);
  const [open, setOpen] = useState(false);
  const [attention, setAttention] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isEmbedded()) return;
    let cancelled = false;
    void fetchGateSession().then(async (s) => {
      if (cancelled || !s.ok || !s.standalone || !s.authenticated) return;
      setUser(s.user);
      if (!s.user.roles.includes(ADMIN_ROLE)) return;
      // The clock and the virus definitions, so an administrator notices a
      // problem without opening the administration page.
      const [auth, adapter] = await Promise.all([
        readAuthSystem().catch(() => null),
        readAdapterSystem().catch(() => null),
      ]);
      if (!cancelled) setAttention(needsAttention(auth, adapter));
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
        className="relative inline-flex max-w-[12rem] items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-2.5 py-1.5 text-xs text-white/85 transition hover:bg-white/10"
      >
        <UserRound className="h-3.5 w-3.5 shrink-0" style={{ color: ACCENT_TEXT }} />
        <span className="hidden truncate sm:inline">{name}</span>
        <ChevronDown
          className={`h-3.5 w-3.5 shrink-0 text-white/50 transition-transform ${open ? "rotate-180" : ""}`}
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
          className="absolute right-0 z-50 mt-2 w-64 overflow-hidden rounded-xl border border-white/10 bg-[#10243A] py-1 shadow-2xl shadow-black/60"
        >
          <div className="border-b border-white/5 px-3 py-2">
            <div className="truncate text-[13px] text-white">{name}</div>
            {user.display_name && (
              <div className="truncate text-[11px] text-white/50">{user.username}</div>
            )}
            {user.drives.length === 0 && (
              <p className="mt-1.5 text-[11px] text-amber-100/90">
                {tm("No rooms are assigned to you yet. Ask the administrator for access.")}
              </p>
            )}
          </div>
          {user.roles.includes(ADMIN_ROLE) && (
            <a
              role="menuitem"
              href={gatePath(ADMIN_PATH)}
              className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-[13px] text-white/80 transition hover:bg-white/5 hover:text-brand-soft"
            >
              <Settings2 className="h-3.5 w-3.5" />
              {tm("Administration")}
              {attention && (
                <span className="ml-auto text-[11px] text-amber-200">{tm("Needs attention")}</span>
              )}
            </a>
          )}
          {user.source !== "ldap" && (
            <a
              role="menuitem"
              href={gatePath(PASSWORD_PATH)}
              className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-[13px] text-white/80 transition hover:bg-white/5 hover:text-brand-soft"
            >
              <KeyRound className="h-3.5 w-3.5" />
              {tm("Change password")}
            </a>
          )}
          <button
            role="menuitem"
            type="button"
            onClick={() => void signOut()}
            className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-[13px] text-white/80 transition hover:bg-white/5 hover:text-brand-soft"
          >
            <LogOut className="h-3.5 w-3.5" />
            {tm("Sign out")}
          </button>
        </div>
      )}
    </div>
  );
}
