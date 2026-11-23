"""Update approval on a Matrix box (E07): the adapter's side of the host updater.

The host updater (matrix_updater/updater.py, root, outside the containers)
downloads signed releases and writes its state to ADAPTER_UPDATES_DIR/state.json.
An administrator approves an installation or a rollback here; the adapter only
writes ADAPTER_UPDATES_DIR/request.json, which a systemd path unit hands to the
updater. The adapter never installs anything itself, and a request can only
name a release the updater already verified and offered.

  ADAPTER_UPDATES_DIR   default empty: no updater on this box, the page says so

The state file comes from another process: only known fields of known types
reach the web UI, strings are cut to length.
"""
import json
import os
import re
import time

VERSION = re.compile(r"^\d{2}\.\d{2}\.\d{1,3}(?:\.\d{1,4})?$")
STATUSES = ("never", "off", "current", "downloading", "ready", "blocked", "error", "installing",
            "rolling_back", "failed", "broken")
STEPS = ("verify", "snapshot", "load", "kit", "install", "data")
LANGUAGES = ("en", "hu", "da", "de")
# A request the updater has not picked up after this long is reported as stuck.
STUCK_SECONDS = 120


class UpdateRefused(Exception):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code = code
        self.status = status


def _text(value, limit=200):
    return value[:limit] if isinstance(value, str) else None


def _version(value):
    return value if isinstance(value, str) and VERSION.match(value) else None


def _label(value):
    return value if isinstance(value, str) and re.match(r"^[0-9A-Za-z.+-]{1,64}$", value) else None


def clean_state(raw):
    """The updater's state, reduced to what the administration page shows."""
    raw = raw if isinstance(raw, dict) else {}
    status = raw.get("status") if raw.get("status") in STATUSES else "never"
    out = {"status": status, "installed": _label(raw.get("installed")), "channel": _text(raw.get("channel"), 16),
           "checked_at": _text(raw.get("checked_at"), 40), "error": _text(raw.get("error"), 500),
           "step": raw.get("step") if raw.get("step") in STEPS else None, "step_at": _text(raw.get("step_at"), 40),
           "interrupted": raw.get("interrupted") is True,
           "available": None, "last": None, "history": []}
    available = raw.get("available")
    if isinstance(available, dict) and _version(available.get("version")):
        notes = available.get("notes") if isinstance(available.get("notes"), dict) else {}
        compat = available.get("compat") if isinstance(available.get("compat"), dict) else {}
        size = available.get("download_bytes")
        out["available"] = {
            "version": available["version"], "label": _label(available.get("label")),
            "notes": {lang: _text(notes.get(lang), 4000) for lang in LANGUAGES if _text(notes.get(lang), 4000)},
            "security": available.get("security") is True,
            "data_copy": compat.get("snapshot") is True, "reindex": compat.get("reindex") is True,
            "published": _text(available.get("published"), 40), "min_from": _version(available.get("min_from")),
            "download_bytes": size if isinstance(size, int) and size >= 0 else None,
            "downloaded": bool(_text(available.get("downloaded_at"), 40))}
    last = raw.get("last")
    if isinstance(last, dict) and _version(last.get("version")):
        previous = last.get("previous") if isinstance(last.get("previous"), dict) else {}
        out["last"] = {"version": last["version"], "previous": _label(previous.get("label")),
                       "data_copy": bool(_text(last.get("snapshot"), 300)),
                       "started_at": _text(last.get("started_at"), 40),
                       "finished_at": _text(last.get("finished_at"), 40),
                       "failed_at": _text(last.get("failed_at"), 40)}
    for item in (raw.get("history") or [])[:20] if isinstance(raw.get("history"), list) else []:
        if isinstance(item, dict) and item.get("result") in ("installed", "rolled_back"):
            out["history"].append({"version": _version(item.get("version")), "at": _text(item.get("at"), 40),
                                   "result": item["result"], "data_restored": item.get("data_restored") is True})
    return out


class UpdateChannel:
    def __init__(self, directory, clock=time.time):
        self.directory = directory
        self.clock = clock

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        directory = (env.get("ADAPTER_UPDATES_DIR") or "").strip()
        return cls(directory or None)

    @property
    def enabled(self):
        return bool(self.directory)

    def _path(self, name):
        return os.path.join(self.directory, name)

    def state(self):
        try:
            with open(self._path("state.json"), "r", encoding="utf-8") as handle:
                raw = json.loads(handle.read(1 << 20))
        except (OSError, ValueError):
            raw = {}
        out = clean_state(raw)
        try:
            with open(self._path("request.json"), "r", encoding="utf-8") as handle:
                pending = json.loads(handle.read(4096))
            at = pending.get("at") if isinstance(pending, dict) and isinstance(pending.get("at"), int) else None
            if at is None:
                at = os.stat(self._path("request.json")).st_mtime
            age = self.clock() - at
            out["pending"] = {"since_seconds": int(max(0, age)), "stuck": age > STUCK_SECONDS}
        except (OSError, ValueError):
            out["pending"] = None
        return out

    def request(self, action, by, version=None, restore_data=False):
        """Hand an approved action to the updater; refused unless the state allows it."""
        if not self.enabled:
            raise UpdateRefused("updates_unavailable", 404)
        state = self.state()
        if state["pending"]:
            raise UpdateRefused("request_pending")
        if action == "install":
            available = state["available"] or {}
            if (state["status"] not in ("ready", "failed") or available.get("version") != version
                    or state["interrupted"]):
                raise UpdateRefused("not_ready")
            body = {"action": "install", "version": version}
        elif action == "rollback":
            last = state["last"]
            if state["status"] not in ("current", "failed", "broken") or not last:
                raise UpdateRefused("nothing_to_roll_back")
            if restore_data and not last["data_copy"]:
                raise UpdateRefused("no_data_copy")
            # After a failed installation the previous version runs again already:
            # going back once more only makes sense for the data copy or a broken box.
            if (state["installed"] == last["previous"] and not restore_data
                    and state["status"] != "broken" and not state["interrupted"]):
                raise UpdateRefused("already_previous")
            body = {"action": "rollback", "restore_data": bool(restore_data)}
        else:
            raise UpdateRefused("unknown_action", 400)
        body.update({"by": by, "at": int(self.clock())})
        temp = self._path(".request.json.tmp")
        with open(temp, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(body))
        os.replace(temp, self._path("request.json"))
        return body
