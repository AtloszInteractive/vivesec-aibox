// Browser side of the administration page of a standalone box (E27 S7).
//
// Two backends, both behind the login gate:
//   * the authentication service (users, directory, clock, sign-in events):
//     POST /auth/admin/<action>; the gate adds the session and checks CSRF;
//   * the adapter (rooms, ACL rules file, virus definitions, file events,
//     organisation templates): /api/v1/ui/admin/*, relayed with the signed
//     identity token.
// Both check the admin role themselves; this module only shapes the calls.

import { followAuthSignal, gatePath, withCsrf } from "./client";

export class AdminError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    readonly detail?: string,
  ) {
    super(code);
    this.name = "AdminError";
  }
}

const TIMEOUT_MS = 30_000;

async function call<T>(url: string, init: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(
      url,
      withCsrf({
        ...init,
        credentials: "same-origin",
        cache: "no-store",
        signal: AbortSignal.timeout(TIMEOUT_MS),
      }),
    );
  } catch {
    throw new AdminError("network", 0);
  }
  followAuthSignal(res);
  let data: Record<string, unknown> = {};
  try {
    data = (await res.json()) as Record<string, unknown>;
  } catch {
    data = {};
  }
  if (!res.ok || data.ok === false) {
    throw new AdminError(
      typeof data.error === "string" ? data.error : `http_${res.status}`,
      res.status,
      typeof data.message === "string" ? data.message : undefined,
    );
  }
  return data as T;
}

function authAdmin<T>(action: string, body: Record<string, unknown> = {}): Promise<T> {
  return call<T>(gatePath(`/auth/admin/${action}`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function adapterAdmin<T>(endpoint: string, body?: Record<string, unknown>): Promise<T> {
  return call<T>(`/api/v1/ui/admin/${endpoint}`, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
}

/* ------------------------------------------------------------------ types */

export type AdminUser = {
  username: string;
  display_name: string;
  source: "local" | "ldap";
  roles: string[];
  groups: string[];
  drives: string[];
  status: "active" | "suspended";
  totp_enrolled: boolean;
  must_change_password: boolean;
  locked: boolean;
  locked_until: number | null;
  created_at: number;
  password_changed_at: number | null;
  last_login_at: number | null;
  directory_synced_at: number | null;
  sessions: number;
};

export type AuthEvent = {
  id: number;
  at: number;
  event: string;
  username: string;
  actor: string;
  client: string;
  detail: string;
};

export type ClockState = {
  state: "synchronized" | "unsynchronized" | "unknown";
  max_error_ms?: number;
  est_error_ms?: number;
  reason?: string;
};

export type AuthSystem = {
  now: number;
  clock: ClockState;
  totp_step_seconds: number;
  totp_tolerance_steps: number;
  directory: { state: "off" | "ready" | "invalid"; problem: string | null };
};

export type GroupMapEntry = { dn: string; name: string; roles: string[] };
export type GroupMap = { groups: GroupMapEntry[]; roles: Record<string, string[]> };

export type DirectoryInfo = {
  state: "off" | "ready" | "invalid";
  problem: string | null;
  urls: string[];
  base_dn: string;
  bind_dn: string;
  user_attribute: string;
  pins: number;
  ca_file: boolean;
  refresh_seconds: number;
  max_stale_seconds: number;
  users: number;
  map: GroupMap | null;
  map_error: string | null;
  map_version: string;
};

export type DirectoryCheck = {
  error?: string;
  service_bind?: string;
  groups?: { dn: string; name: string; roles: string[]; found: boolean }[];
  user?: {
    username: string;
    refused?: string;
    display_name?: string;
    groups?: string[];
    roles?: string[];
    drives?: string[];
    may_sign_in?: boolean;
  };
};

export type ScannerState = {
  reachable: boolean;
  engine: string | null;
  database: number | null;
  database_time: number | null;
  age_days: number | null;
  stale: boolean;
};

export type AdapterSystem = {
  now: number;
  scan: string;
  clamd: ScannerState;
  max_age_days: number;
  store: boolean;
  acl: {
    mode: string;
    configured: boolean;
    rules_error: string | null;
    rule_paths: number;
    default_classification: string | null;
    default_clearance: string | null;
  };
};

export type Room = { path: string; name: string; stored: boolean; indexed: boolean; files: number };

export type AclNode = {
  allow?: string[];
  deny?: string[];
  inherit?: boolean;
  classification?: string;
};

export type AclRules = {
  paths: Record<string, AclNode>;
  clearance?: Record<string, string>;
  default_classification?: string;
};

export type AclState = {
  configured: boolean;
  exists?: boolean;
  version?: string;
  rules?: AclRules | null;
  error?: string | null;
  levels: string[];
  settings: {
    default_classification: string | null;
    box_default_classification: string | null;
    default_clearance: string | null;
  };
};

export type StoreEvent = {
  ts: number;
  action: string;
  user: string;
  path: string;
  [detail: string]: unknown;
};

/* ------------------------------------------------------------------ users */

export const listUsers = () => authAdmin<{ users: AdminUser[] }>("users").then((r) => r.users);

export type UserFields = {
  display_name?: string;
  roles?: string[];
  groups?: string[];
  drives?: string[];
};

export const createUser = (
  input: UserFields & { username: string; password: string; temporary: boolean },
) => authAdmin<{ user: AdminUser }>("users/create", input).then((r) => r.user);

export const updateUser = (
  username: string,
  fields: UserFields & { status?: "active" | "suspended" },
) => authAdmin<{ user: AdminUser }>("users/update", { username, ...fields }).then((r) => r.user);

export const deleteUser = (username: string) =>
  authAdmin<Record<string, never>>("users/delete", { username });

export const resetTotp = (username: string) =>
  authAdmin<{ user: AdminUser }>("users/reset-totp", { username }).then((r) => r.user);

export const unlockUser = (username: string) =>
  authAdmin<{ user: AdminUser }>("users/unlock", { username }).then((r) => r.user);

export const resetPassword = (username: string, password: string, temporary: boolean) =>
  authAdmin<{ user: AdminUser }>("users/reset-password", { username, password, temporary }).then(
    (r) => r.user,
  );

/* ------------------------------------------------------------------ system */

export type AuthSystemReading = AuthSystem & {
  /** box clock minus this browser's clock, in seconds (round trip halved) */
  offsetSeconds: number;
  roundTripMs: number;
};

export async function readAuthSystem(): Promise<AuthSystemReading> {
  const sent = Date.now();
  const system = await authAdmin<AuthSystem>("system");
  const received = Date.now();
  return {
    ...system,
    offsetSeconds: system.now - (sent + received) / 2000,
    roundTripMs: received - sent,
  };
}

export const readAdapterSystem = () => adapterAdmin<AdapterSystem>("system");

/* ------------------------------------------------------------------ directory */

export const readDirectory = () =>
  authAdmin<{ directory: DirectoryInfo }>("directory").then((r) => r.directory);

export const saveGroupMap = (map: GroupMap, base: string) =>
  authAdmin<{ directory: DirectoryInfo }>("directory/map", { map, base }).then((r) => r.directory);

export const checkDirectory = (username?: string) =>
  authAdmin<{ check: DirectoryCheck }>("directory/check", username ? { username } : {}).then(
    (r) => r.check,
  );

/* ------------------------------------------------------------------ rooms and ACL */

export const listRooms = () => adapterAdmin<{ rooms: Room[]; prefix: string }>("rooms");

export const createRoom = (name: string) =>
  adapterAdmin<{ room: Room }>("rooms/create", { name }).then((r) => r.room);

export const deleteRoom = (path: string) =>
  adapterAdmin<{ rules_removed: number }>("rooms/delete", { path });

export const readAcl = () => adapterAdmin<AclState>("acl");

export const saveAcl = (rules: AclRules, base: string) =>
  adapterAdmin<{ version: string; rules: AclRules }>("acl", { rules, base });

/* ------------------------------------------------------------------ events */

export const listAuthEvents = (options: { limit?: number; username?: string; before?: number }) =>
  authAdmin<{ events: AuthEvent[] }>("events", options).then((r) => r.events);

export const listStoreEvents = (limit: number) =>
  adapterAdmin<{ events: StoreEvent[] }>(`events?limit=${limit}`).then((r) => r.events);

/* ------------------------------------------------------------------ templates (E18) */

export type FrameSummary = {
  page: { width_mm: number; height_mm: number; landscape: boolean };
  margins_mm: { top: number; right: number; bottom: number; left: number };
  headers: number;
  footers: number;
  pictures: number;
  links: number;
  fonts: { headings?: string; body?: string };
  added_styles: string[];
  bullets: "template" | "symbol";
  first_page: boolean;
  even_odd: boolean;
};

export type TemplateVersion = {
  id: string;
  name: string;
  size: number;
  sha256: string;
  uploaded_by: string;
  uploaded_at: number;
  summary: FrameSummary;
};

export type LayoutColumn = { column: string | null; label: string | null; width: number | null };

type Stamped<T> = { value: T; updated_by: string; updated_at: number } | null;

export type TemplatesState = {
  configured: boolean;
  error: string | null;
  docx: {
    active: string | null;
    versions: TemplateVersion[];
    activated_by?: string;
    activated_at?: number;
  };
  xlsx_layout: Stamped<LayoutColumn[]>;
  scan: string;
  columns: { id: string; labels: Record<"en" | "hu" | "da" | "de", string> }[];
  required: string[];
  limits: {
    max_bytes: number;
    max_columns: number;
    max_label: number;
  };
};

export type GeneratedFile = { name: string; content_type: string; content_b64: string };

/** A file sent as the raw request body; its name travels percent-encoded. */
function adapterUpload<T>(endpoint: string, file: File): Promise<T> {
  return call<T>(`/api/v1/ui/admin/${endpoint}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/octet-stream",
      "X-Template-Name": encodeURIComponent(file.name),
    },
    body: file,
  });
}

export const readTemplates = () => adapterAdmin<TemplatesState>("templates");

export const uploadWordTemplate = (file: File) =>
  adapterUpload<TemplatesState & { version: TemplateVersion }>("templates/docx", file);

export const activateWordTemplate = (id: string | null) =>
  adapterAdmin<TemplatesState>("templates/docx/activate", { id });

export const previewWordTemplate = (id: string | null, text: string, title: string) =>
  adapterAdmin<GeneratedFile>("templates/docx/preview", { id, text, title });

export const readSampleSheet = (file: File) =>
  adapterUpload<{ columns: LayoutColumn[] }>("templates/xlsx/read", file).then((r) => r.columns);

export const saveLayout = (columns: LayoutColumn[] | null) =>
  adapterAdmin<TemplatesState>("templates/xlsx", { columns });

/* ------------------------------------------------------------------ company profile (E12) */

export type FactState = "draft" | "effective" | "expired" | "archived";

export type FactSource = { path: string; page: number | null; quote: string };

export type FactInput = {
  id?: string;
  group: string;
  field: string;
  label: string;
  value: string;
  classification: string;
  valid_from: string | null;
  valid_to: string | null;
};

export type CompanyFact = FactInput & {
  id: string;
  state: FactState;
  source: FactSource | null;
  replaces: string | null;
  created_by: string;
  created_at: number;
  updated_by: string;
  updated_at: number;
};

export type ProfileGroup = {
  id: string;
  label: string;
  fields: { id: string; label: string; repeatable: boolean; named: boolean }[];
};

export type Extraction = {
  state: "idle" | "queued" | "running" | "done" | "cancelled" | "failed";
  user?: string;
  documents?: number;
  done?: number;
  parts?: number;
  proposals?: number;
  failed?: string[];
  error?: string;
  started_at?: number;
  finished_at?: number;
};

export type CompanyState = {
  configured: boolean;
  error: string | null;
  revision: number;
  facts: CompanyFact[];
  schema: ProfileGroup[];
  states: FactState[];
  levels: string[];
  default_classification: string | null;
  extraction: Extraction;
  limits: { max_label: number; max_value: number; max_documents: number };
};

export type FactHistoryEntry = {
  seq: number;
  ts: number;
  user: string;
  action: "create" | "edit" | "state" | "delete" | "propose" | "import";
  fact: string;
  before: CompanyFact | null;
  after: CompanyFact | null;
};

export type ProfileDocument = { path: string; name: string; room: string };

export const readCompany = () => adapterAdmin<CompanyState>("company");

export const saveFact = (base: number, fact: FactInput) =>
  adapterAdmin<CompanyState>("company/fact", { base, fact });

export const setFactState = (base: number, id: string, state: FactState) =>
  adapterAdmin<CompanyState>("company/state", { base, id, state });

export const deleteFact = (base: number, id: string) =>
  adapterAdmin<CompanyState>("company/delete", { base, id });

export const restoreFact = (base: number, id: string, seq: number) =>
  adapterAdmin<CompanyState>("company/restore", { base, id, seq });

export const readFactHistory = (id: string) =>
  adapterAdmin<{ history: FactHistoryEntry[] }>(
    `company/history?id=${encodeURIComponent(id)}`,
  ).then((r) => r.history);

export const listProfileDocuments = (query: string) =>
  adapterAdmin<{ documents: ProfileDocument[] }>(
    `company/documents?q=${encodeURIComponent(query)}`,
  ).then((r) => r.documents);

export const startExtraction = (paths: string[]) =>
  adapterAdmin<CompanyState>("company/extract", { paths });

export const cancelExtraction = () => adapterAdmin<CompanyState>("company/extract/cancel", {});
