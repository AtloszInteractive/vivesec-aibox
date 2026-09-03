// Server-only document ACL registry + user context for the bridge.
//
// The bridge needs two things to run the ACL pre-filter (spec §2, invariant 2):
//   1. what each indexed document is (file_id + acl_scope + sensitivity)
//   2. who is asking (granted scopes + clearance)
//
// On the real Box both come from the ViVeSec Box (document ACLs + the signed-in
// user's grants). Until that wiring exists, this provides a static registry that
// mirrors the demo corpus + a permissive demo user, so the ACL machinery is
// exercised end-to-end. Replace `getDocRegistry` / `resolveUser` with real
// lookups when auth + the Box ACL feed land.

import type { DocAcl, UserContext } from "../rag/acl";

// file_id MUST equal what was ingested into rag-engine AND the UI DriveFile.id,
// so citations open the right document (ViveSecApp INITIAL_FILES ids).
const REGISTRY: (DocAcl & { filename: string })[] = [
  { fileId: "f1", filename: "Contract_2025_Final.pdf", aclScope: ["legal"], sensitivity: "confidential" },
  { fileId: "f2", filename: "Q4_Financials.xlsx", aclScope: ["finance"], sensitivity: "confidential" },
  { fileId: "f4", filename: "Board_Meeting_Transcript_2025-11-12.txt", aclScope: ["board"], sensitivity: "restricted" },
  { fileId: "f5", filename: "Security_Audit_Report.pdf", aclScope: ["security"], sensitivity: "confidential" },
  { fileId: "f6", filename: "Vendor_Risk_Matrix.md", aclScope: ["risk", "procurement"], sensitivity: "internal" },
  { fileId: "fh1", filename: "Employee_Handbook_v4.docx", aclScope: ["hr", "all"], sensitivity: "internal" },
  { fileId: "fsla", filename: "Cloud_Infrastructure_SLA_2026.pdf", aclScope: ["it", "all"], sensitivity: "internal" },
  { fileId: "fv1", filename: "Vendor_Agreement_2025.pdf", aclScope: ["legal", "procurement"], sensitivity: "confidential" },
  { fileId: "fa1", filename: "Q4_Infrastructure_Security_Audit.pdf", aclScope: ["security"], sensitivity: "confidential" },
];

export function getDocRegistry(): DocAcl[] {
  return REGISTRY.map(({ fileId, aclScope, sensitivity }) => ({ fileId, aclScope, sensitivity }));
}

export function filenameFor(fileId: string): string | undefined {
  return REGISTRY.find((d) => d.fileId === fileId)?.filename;
}

/**
 * Resolve the asking user. DEMO: full access (every scope, top clearance) so the
 * showcase corpus is fully reachable. Real impl: read the session/grants here and
 * return only what the signed-in user is entitled to — the rest follows
 * automatically via the ACL pre-filter.
 */
export function resolveUser(): UserContext {
  const allScopes = Array.from(new Set(REGISTRY.flatMap((d) => d.aclScope)));
  return { grantedScopes: allScopes, clearance: "restricted" };
}
