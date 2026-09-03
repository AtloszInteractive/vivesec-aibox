// ACL pre-filter — the bridge owns the authorization decision (spec §2, invariant 2).
//
// The rag-engine NEVER decides who may see what. The HÍD computes the set of
// allowed file_ids (user grants ∩ document scope ∩ clearance) and passes it to
// /search as a HARD pre-filter. The engine only narrows, never widens, that set.
//
// This module is pure + framework-free so it is trivially unit-testable.

import { SENSITIVITY_ORDER, type Sensitivity } from "./contract";

// What the bridge knows about each indexed document (from the ViVeSec Box ACLs).
export type DocAcl = {
  fileId: string;
  aclScope: string[]; // scope tags stamped at ingest (e.g. ["finance", "board"])
  sensitivity: Sensitivity;
};

// What the bridge knows about the asking user (resolved from the session).
export type UserContext = {
  // Scopes the user is granted (e.g. ["finance"]). Empty => no document scopes.
  grantedScopes: string[];
  // Highest sensitivity the user is cleared for. Default: "internal".
  clearance: Sensitivity;
};

function sensitivityRank(s: Sensitivity): number {
  return SENSITIVITY_ORDER.indexOf(s);
}

/**
 * A user may see a document iff:
 *   1. clearance covers the document's sensitivity, AND
 *   2. the user holds at least one of the document's scope tags.
 * A document with NO scope tags is treated as scope-restricted (deny) — fail closed.
 */
export function canAccess(user: UserContext, doc: DocAcl): boolean {
  if (sensitivityRank(doc.sensitivity) > sensitivityRank(user.clearance)) {
    return false;
  }
  if (doc.aclScope.length === 0) {
    return false; // fail closed: an unscoped doc is not broadly readable
  }
  const granted = new Set(user.grantedScopes);
  return doc.aclScope.some((tag) => granted.has(tag));
}

/**
 * Compute the HARD pre-filter passed to /search.allowed_file_ids.
 * Returns only the file_ids the user is allowed to retrieve from.
 */
export function computeAllowedFileIds(user: UserContext, docs: readonly DocAcl[]): string[] {
  return docs.filter((d) => canAccess(user, d)).map((d) => d.fileId);
}
