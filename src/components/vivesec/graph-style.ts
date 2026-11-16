import type { GraphEdge, GraphEntityType } from "@/lib/api/graph.functions";

export const TYPE_COLOR: Record<GraphEntityType, string> = {
  person: "#B48CF5",
  organization: "#60A5FA",
  place: "#F59E0B",
  identifier: "#CBD5E1",
  event: "#FB7185",
  object: "#2DD4BF",
};

export const TYPE_NAME: Record<GraphEntityType, string> = {
  person: "Person",
  organization: "Organisation",
  place: "Place",
  identifier: "Identifier",
  event: "Event",
  object: "Object",
};

/** One key per pair of nodes, whatever the direction of its edges. */
export const pairKey = (e: GraphEdge) => (e.a < e.b ? `${e.a}|${e.b}` : `${e.b}|${e.a}`);

/** What the domain profile says about a relation type: whether it has a
 *  direction (not symmetric) and whether it lasts until it ends (a presence
 *  at a place: a placement without an end date is still in force). */
export type RelationInfo = { directed: boolean; ongoing: boolean };
