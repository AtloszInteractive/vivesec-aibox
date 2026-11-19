import {
  Banknote,
  BadgeCheck,
  Building,
  Building2,
  CalendarClock,
  Car,
  ClipboardList,
  Contact,
  DoorClosed,
  Eye,
  Hash,
  House,
  IdCard,
  Landmark,
  LayoutGrid,
  Mail,
  MapPin,
  Package,
  Phone,
  TriangleAlert,
  User,
  UserLock,
  UserRound,
  Users,
  type LucideIcon,
} from "lucide-react";
import type { GraphEdge, GraphEntityType } from "@/lib/api/graph.functions";
import type { EdgeSource } from "./graph-model";

export const TYPE_COLOR: Record<GraphEntityType, string> = {
  person: "#B48CF5",
  organization: "#60A5FA",
  place: "#F59E0B",
  identifier: "#CBD5E1",
  event: "#FB7185",
  object: "#2DD4BF",
};

/** Opacity of what is not highlighted. */
export const FADED = 0.13;
/** Informant data (E29). */
export const PROTECTED = "#F43F5E";
/** Line styles by where a relation comes from. */
export const DASH: Record<EdgeSource, string | undefined> = {
  register: undefined,
  text: "7 4",
  comention: "2 3",
};

export const SUBTYPE_ICON: Record<string, LucideIcon> = {
  inmate: UserLock,
  visitor: UserRound,
  staff: BadgeCheck,
  contact: Contact,
  institution: Landmark,
  facility: Building,
  unit: LayoutGrid,
  cell: DoorClosed,
  address: House,
  phone: Phone,
  id_card: IdCard,
  vehicle_plate: Car,
  bank_account: Banknote,
  case_number: Hash,
  email: Mail,
  incident: TriangleAlert,
  meeting: Users,
  intel_report: Eye,
  odin_entry: ClipboardList,
};

export const TYPE_ICON: Record<GraphEntityType, LucideIcon> = {
  person: User,
  organization: Building2,
  place: MapPin,
  identifier: Hash,
  event: CalendarClock,
  object: Package,
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
