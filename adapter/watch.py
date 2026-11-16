"""Watchlist pilot (proactive chat): an analyst marks an entity of the relationship
graph, and later the box tells what is new about it -- a relation not seen before,
or a relation that more documents back than before.

Off unless ADAPTER_WATCHLIST=on (anything else: off). Whether such a list may be
kept, and for how long, is the customer's legal decision; the pilot exists to be
tried, not to be on by default.

Per user and scope (the conversations' isolation: conversations.scope_key) it
keeps the entity id, its label, the case reference it was added under and a
snapshot {relation type, other entity and its name: number of backing documents}. Nothing runs in the
background and nothing is scored: a check is a graph call in the caller's
current scope and access, under the request's purpose, and the caller logs it.
A check leaves the snapshot alone; `seen` takes over what the last check found,
so a change that arrives in between is never acknowledged unseen.

Entries expire ADAPTER_WATCH_MAX_DAYS (30) after they were added; at most
ADAPTER_WATCH_MAX_ITEMS (20) per user and scope. ADAPTER_WATCH_DIR keeps them
across restarts; without it they live in memory only.
"""
import json
import os
import threading
import time
import uuid

from conversations import scope_key

DEFAULT_MAX_DAYS = 30
DEFAULT_MAX_ITEMS = 20
# One-hop relations of a watched entity read per check (graph neighborhood limit).
NEIGHBOURS = 2000


def enabled_from_env(env=None):
    env = os.environ if env is None else env
    return (env.get("ADAPTER_WATCHLIST", "off") or "off").strip().lower() == "on"


def _bounded(env, name, default, low, high):
    try:
        value = int(env.get(name, "") or default)
    except ValueError:
        return default
    return max(low, min(value, high))


class WatchFull(Exception):
    pass


def _key(edge, entity_id, nodes):
    """A relation as the analyst sees it: its type and the other entity with its
    name. Ids of document text are per document position, so a document read
    again can give a relation id, or an entity id, to another name."""
    other = edge["b"] if edge.get("a") == entity_id else edge.get("a")
    label = " ".join(str((nodes.get(other) or {}).get("label") or "").casefold().split())
    return "%s|%s|%s" % (edge.get("type") or "", other or "", label)


def _documents(edges, entity_id, nodes):
    out = {}
    for edge in edges or []:
        if entity_id in (edge.get("a"), edge.get("b")) and edge.get("type"):
            docs = out.setdefault(_key(edge, entity_id, nodes), set())
            docs.update(ev.get("doc_id") for ev in edge.get("evidence") or [] if ev.get("doc_id"))
    return out


def snapshot(edges, entity_id, nodes=()):
    """{relation type|other entity|its name: number of documents backing it} of
    the entity's own relations."""
    nodes = {n["id"]: n for n in nodes if n}
    return {key: len(docs) for key, docs in _documents(edges, entity_id, nodes).items()}


def diff(item, neighbourhood):
    """What changed about a watched entity since its snapshot, from a one-hop
    neighbourhood answer: new relations, relations with more backing documents,
    and how many snapshot relations the caller does not see now."""
    entity_id = item["entity_id"]
    nodes = {n["id"]: n for n in neighbourhood.get("nodes") or [] if n}
    edges = neighbourhood.get("edges") or []
    docs = _documents(edges, entity_id, nodes)
    dates = {}
    for edge in edges:
        if entity_id in (edge.get("a"), edge.get("b")) and edge.get("type"):
            dates.setdefault(_key(edge, entity_id, nodes), []).append(edge)
    before = item.get("snapshot") or {}
    new, more = [], []
    for key, doc_ids in docs.items():
        rel_type, other_id, _ = key.split("|", 2)
        other = nodes.get(other_id) or {}
        starts = sorted(e.get("start") for e in dates[key] if e.get("start"))
        ends = sorted(e.get("end") for e in dates[key] if e.get("end"))
        row = {"id": key, "type": rel_type, "start": starts[0] if starts else None,
               "end": ends[-1] if ends else None,
               "weak": all(e.get("weak") for e in dates[key]), "documents": len(doc_ids),
               "doc_ids": sorted(doc_ids),
               "other": {k: other.get(k) for k in ("id", "label", "type", "subtype")}}
        if key not in before:
            new.append(row)
        elif len(doc_ids) > before[key]:
            more.append(dict(row, before=before[key]))
    order = lambda r: (r.get("start") or "", r["id"])  # noqa: E731
    return {"new": sorted(new, key=order), "more": sorted(more, key=order),
            "gone": sum(1 for key in before if key not in docs),
            "truncated": bool(neighbourhood.get("truncated")),
            "snapshot": {key: len(d) for key, d in docs.items()}}


class WatchStore:
    def __init__(self, directory=None, max_days=DEFAULT_MAX_DAYS,
                 max_items=DEFAULT_MAX_ITEMS, clock=time.time):
        self.directory = directory
        self.max_days = max_days
        self.max_items = max_items
        self._clock = clock
        self._lock = threading.Lock()
        self._mem = {}

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        return cls(directory=(env.get("ADAPTER_WATCH_DIR") or "").strip() or None,
                   max_days=_bounded(env, "ADAPTER_WATCH_MAX_DAYS", DEFAULT_MAX_DAYS, 1, 365),
                   max_items=_bounded(env, "ADAPTER_WATCH_MAX_ITEMS", DEFAULT_MAX_ITEMS, 1, 100))

    def _path(self, key):
        return os.path.join(self.directory, key + ".json") if self.directory else None

    def _read(self, key, user, scope):
        """The live entries of one user and scope (caller holds the lock)."""
        items = self._mem.get(key)
        if items is None:
            items = []
            path = self._path(key)
            if path and os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        data = json.load(handle)
                    if data.get("user") == (user or "") and data.get("scope") == (scope or ""):
                        items = list(data.get("items") or [])
                except (OSError, ValueError):
                    items = []
        now = self._clock()
        live = [i for i in items if float(i.get("expires") or 0) > now]
        self._mem[key] = live
        if len(live) != len(items):
            self._write(key, user, scope, live)
        return live

    def _write(self, key, user, scope, items):
        self._mem[key] = items
        path = self._path(key)
        if not path:
            return
        os.makedirs(self.directory, exist_ok=True)
        if not items:
            try:
                os.remove(path)
            except OSError:
                pass
            return
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"user": user or "", "scope": scope or "", "items": items}, handle,
                      ensure_ascii=False)
        os.replace(tmp, path)

    @staticmethod
    def public(item):
        """An entry as the UI sees it: no snapshot, no pending check."""
        out = {k: item.get(k) for k in ("id", "entity_id", "label", "type", "subtype",
                                         "case_ref", "created", "expires", "checked", "seen")}
        out["relations"] = len(item.get("snapshot") or {})
        return out

    def list(self, user, scope):
        with self._lock:
            return [dict(i) for i in self._read(scope_key(user, scope), user, scope)]

    def get(self, user, scope, watch_id):
        return next((i for i in self.list(user, scope) if i["id"] == watch_id), None)

    def get_entity(self, user, scope, entity_id):
        return next((i for i in self.list(user, scope) if i["entity_id"] == entity_id), None)

    def add(self, user, scope, entity, edges, case_ref=None, nodes=()):
        """Watch an entity (a graph node) with its current relations; the entry
        it already has when it is watched. Raises WatchFull at the cap."""
        key = scope_key(user, scope)
        with self._lock:
            items = self._read(key, user, scope)
            existing = next((i for i in items if i["entity_id"] == entity["id"]), None)
            if existing is not None:
                return dict(existing), False
            if len(items) >= self.max_items:
                raise WatchFull("at most %d watched entities" % self.max_items)
            now = self._clock()
            item = {"id": uuid.uuid4().hex[:16], "entity_id": entity["id"],
                    "label": entity.get("label") or "", "type": entity.get("type"),
                    "subtype": entity.get("subtype") or "", "case_ref": case_ref or None,
                    "created": now, "expires": now + self.max_days * 86400.0,
                    "checked": None, "seen": now,
                    "snapshot": snapshot(edges, entity["id"], nodes)}
            self._write(key, user, scope, items + [item])
            return dict(item), True

    def remove(self, user, scope, watch_id):
        key = scope_key(user, scope)
        with self._lock:
            items = self._read(key, user, scope)
            kept = [i for i in items if i["id"] != watch_id]
            if len(kept) == len(items):
                return False
            self._write(key, user, scope, kept)
            return True

    def checked(self, user, scope, watch_id, found):
        """Remember what a check found (`found`: a snapshot), for `seen`."""
        key = scope_key(user, scope)
        with self._lock:
            items = self._read(key, user, scope)
            for item in items:
                if item["id"] == watch_id:
                    item["checked"] = self._clock()
                    item["pending"] = found
            self._write(key, user, scope, items)

    def seen(self, user, scope, watch_id):
        """Take over what the last check found; False when there is no such entry
        or it was not checked since."""
        key = scope_key(user, scope)
        with self._lock:
            items = self._read(key, user, scope)
            item = next((i for i in items if i["id"] == watch_id), None)
            if item is None or item.get("pending") is None:
                return False
            # A relation not visible now (scope, access) stays in the snapshot:
            # seeing it again later is not news.
            item["snapshot"] = dict(item.get("snapshot") or {}, **item.pop("pending"))
            item["seen"] = self._clock()
            self._write(key, user, scope, items)
            return True
