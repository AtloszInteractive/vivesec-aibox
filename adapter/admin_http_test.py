"""E27 S7 over HTTP: the standalone administration endpoints of the adapter --
rooms, the ACL rules file, the virus definitions and the event log. The RAG
and clamd are fakes."""
import json
import os
import time
import unittest
from unittest.mock import patch

import acl
import acl_admin
import clamd
import integration
from store_http_test import NOW, OTHER, ROOM, FakeScanner, StandaloneHttpCase

PREFIX = "/storage/drives"


class VersionedScanner(FakeScanner):
    reply = "ClamAV 1.4.6/28177/Sun Nov  8 06:23:52 2026"

    def version(self):
        if self.down:
            raise clamd.ScanUnavailable("down")
        return self.reply


class AdminHttpTest(StandaloneHttpCase):
    def setUp(self):
        super().setUp()
        self.scanner = VersionedScanner()
        patcher = patch.object(self.service, "CLAMD", self.scanner)
        patcher.start()
        self.addCleanup(patcher.stop)

    def admin(self, method, path, body=None, **who):
        who.setdefault("drives", ())
        who.setdefault("drive", ROOM + "/")
        headers = self.headers(**who)
        if not who["drives"]:
            headers.pop("VVS-Drive")
        data = None if body is None else json.dumps(body).encode("utf-8")
        if data is not None:
            headers["Content-Type"] = "application/json"
        status, raw, _ = self.request(method, path, data, headers)
        return status, (json.loads(raw.decode("utf-8")) if raw else {})

    def acl_state(self):
        status, body = self.admin("GET", "/api/v1/ui/admin/acl")
        self.assertEqual(status, 200, body)
        return body

    # -- access ------------------------------------------------------------------
    def test_only_administrators_in_standalone_mode(self):
        paths = (("GET", "/api/v1/ui/admin/system", None), ("GET", "/api/v1/ui/admin/rooms", None),
                 ("GET", "/api/v1/ui/admin/acl", None), ("GET", "/api/v1/ui/admin/events", None),
                 ("POST", "/api/v1/ui/admin/rooms/create", {"name": "x"}),
                 ("POST", "/api/v1/ui/admin/rooms/delete", {"path": ROOM}),
                 ("POST", "/api/v1/ui/admin/acl", {"rules": {}, "base": "x"}))
        for method, path, body in paths:
            status, answer = self.admin(method, path, body, roles=("analyst",))
            self.assertEqual((status, answer.get("error")), (403, "forbidden"), path)
            headers = self.headers()
            headers.pop("VVS-Identity")
            status, _raw, _ = self.request(method, path, json.dumps(body or {}).encode(), headers)
            self.assertEqual(status, 401, path)
            with patch.object(self.service, "INTEGRATION", integration.MODE_VIVESECBOX):
                status, _ = self.admin(method, path, body)
                self.assertEqual(status, 404, path)
        self.assertFalse(os.path.exists(os.path.join(self.store.root, "rooms", "x")))

    def test_an_admin_without_rooms_can_administer(self):
        status, body = self.admin("GET", "/api/v1/ui/admin/rooms")
        self.assertEqual((status, body["rooms"]), (200, []))
        # A drive the token does not grant is still refused.
        status, _ = self.admin("GET", "/api/v1/ui/admin/rooms", drives=(OTHER + "/",))
        self.assertEqual(status, 403)

    # -- system ------------------------------------------------------------------
    def test_system_reports_definition_age(self):
        database_time = 1794119032  # Sun Nov  8 06:23:52 2026 UTC
        with patch.object(time, "time", lambda: database_time + 3 * 86400):
            status, body = self.admin("GET", "/api/v1/ui/admin/system")
        self.assertEqual(status, 200)
        self.assertEqual(body["clamd"], {"reachable": True, "engine": "1.4.6", "database": 28177,
                                         "database_time": database_time, "age_days": 3.0,
                                         "stale": False})
        self.assertEqual((body["max_age_days"], body["scan"]), (7, "required"))
        self.assertTrue(body["acl"]["configured"])
        with patch.object(time, "time", lambda: database_time + 8 * 86400):
            status, body = self.admin("GET", "/api/v1/ui/admin/system")
        self.assertTrue(body["clamd"]["stale"])
        self.scanner.down = True
        status, body = self.admin("GET", "/api/v1/ui/admin/system")
        self.assertEqual((body["clamd"]["reachable"], body["clamd"]["age_days"]), (False, None))

    # -- rooms -------------------------------------------------------------------
    def test_create_list_and_delete_rooms(self):
        status, body = self.admin("POST", "/api/v1/ui/admin/rooms/create", {"name": "Ügyek"})
        self.assertEqual(status, 200, body)
        room = PREFIX + "/Ügyek"
        self.assertEqual(body["room"], {"path": room, "name": "Ügyek", "stored": True,
                                        "indexed": True, "files": 0})
        self.assertEqual(self.store.kind(room), "folder")
        self.assertIn(room, self.rag.paths("/index/upsert/directory"))
        status, body = self.admin("POST", "/api/v1/ui/admin/rooms/create", {"name": "Ügyek"})
        self.assertEqual((status, body["error"]), (409, "exists"))
        for bad in ("a/b", "..", ".hidden", "", "x\u202e"):
            status, body = self.admin("POST", "/api/v1/ui/admin/rooms/create", {"name": bad})
            self.assertEqual((status, body["error"]), (400, "invalid_path"), bad)
        # A corpus known only from the index is listed too, with its files.
        self.mirror.upsert(OTHER, file=False, mtime=None, size=None)
        self.mirror.upsert(OTHER + "/a.pdf", file=True, mtime=1, size=1)
        status, body = self.admin("GET", "/api/v1/ui/admin/rooms")
        self.assertEqual([(r["name"], r["stored"], r["indexed"], r["files"]) for r in body["rooms"]],
                         [("other", False, True, 1), ("Ügyek", True, True, 0)])
        status, body = self.admin("POST", "/api/v1/ui/admin/rooms/delete", {"path": OTHER})
        self.assertEqual((status, body["error"]), (409, "not_empty"))
        status, body = self.admin("POST", "/api/v1/ui/admin/rooms/delete", {"path": OTHER + "/x"})
        self.assertEqual((status, body["error"]), (400, "not_a_room"))
        self.store.make_dirs(room + "/sub")
        status, body = self.admin("POST", "/api/v1/ui/admin/rooms/delete", {"path": room})
        self.assertEqual(status, 200, body)
        self.assertIsNone(self.store.kind(room))
        self.assertIn(room, self.rag.paths("/index/drop/tree"))
        status, body = self.admin("POST", "/api/v1/ui/admin/rooms/delete", {"path": room})
        self.assertEqual(status, 404)
        actions = [e["action"] for e in reversed(self.store.tail_events())]
        self.assertEqual(actions, ["room_create", "room_delete"])

    def test_a_room_with_files_or_pending_uploads_is_kept(self):
        self.admin("POST", "/api/v1/ui/admin/rooms/create", {"name": "cases"})
        status, _ = self.upload(ROOM + "/a.txt")
        self.assertEqual(status, 202)
        status, body = self.admin("POST", "/api/v1/ui/admin/rooms/delete", {"path": ROOM})
        self.assertEqual((status, body["error"]), (409, "busy"))
        self.worker.drain()
        status, body = self.admin("POST", "/api/v1/ui/admin/rooms/delete", {"path": ROOM})
        self.assertEqual((status, body["error"]), (409, "not_empty"))
        self.assertEqual(self.store.kind(ROOM + "/a.txt"), "file")

    def test_deleting_a_room_drops_its_rules(self):
        self.admin("POST", "/api/v1/ui/admin/rooms/create", {"name": "cases"})
        state = self.acl_state()
        rules = {"paths": {ROOM: {"allow": ["role:admin"]}, OTHER: {"deny": ["user:bob"]}}}
        status, body = self.admin("POST", "/api/v1/ui/admin/acl", {"rules": rules, "base": state["version"]})
        self.assertEqual(status, 200, body)
        status, body = self.admin("POST", "/api/v1/ui/admin/rooms/delete", {"path": ROOM})
        self.assertEqual((status, body["rules_removed"]), (200, 1))
        self.assertEqual(list(self.acl_state()["rules"]["paths"]), [OTHER])

    # -- ACL rules ---------------------------------------------------------------
    def test_rules_are_validated_normalised_and_versioned(self):
        state = self.acl_state()
        self.assertEqual((state["configured"], state["exists"], state["rules"]), (True, True, {}))
        self.assertEqual(state["levels"][0], "public")
        nfd = "/storage/drives/cases/U\u0308gyek"
        rules = {"paths": {nfd: {"allow": ["Group:Nyomozók", "role:admin"], "deny": [],
                                 "inherit": True, "classification": "bizalmas"},
                           ROOM + "/": {"deny": ["user:Bob"]},
                           OTHER: {"allow": None, "deny": [], "inherit": True}},
                 "clearance": {"default": "internal", "role:admin": "personal"},
                 "default_classification": "internal"}
        status, body = self.admin("POST", "/api/v1/ui/admin/acl", {"rules": rules, "base": state["version"]})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["rules"], {
            "paths": {ROOM: {"deny": ["user:bob"]},
                      "/storage/drives/cases/Ügyek": {"allow": ["group:nyomozók", "role:admin"],
                                                      "classification": "confidential"}},
            "clearance": {"default": "internal", "role:admin": "personal"},
            "default_classification": "internal"})
        # The policy applies it at once: bob no longer sees the room.
        self.assertFalse(self.service.ACL.allows(ROOM + "/a.txt", self.service.ACL.access_for("bob")))
        self.assertEqual(self.acl_state()["version"], body["version"])
        status, body = self.admin("POST", "/api/v1/ui/admin/acl", {"rules": rules, "base": state["version"]})
        self.assertEqual((status, body["error"]), (409, "rules_changed"))
        actions = [e["action"] for e in self.store.tail_events()]
        self.assertEqual(actions, ["acl_update"])

    def test_invalid_rules_are_refused_whole(self):
        state = self.acl_state()
        for rules in ({"paths": {"/etc/passwd": {}}}, {"paths": {ROOM + "/../x": {}}},
                      {"paths": {ROOM: {"allow": ["admins"]}}},
                      {"paths": {ROOM: {"allow": "role:admin"}}},
                      {"paths": {ROOM: {"classification": "top secret"}}},
                      {"paths": {ROOM: {"inherit": "no"}}},
                      {"paths": {ROOM: {"owner": "x"}}},
                      {"paths": {ROOM: {}, ROOM + "/": {}}},
                      {"clearance": {"everyone": "personal"}},
                      {"clearance": {"role:x": "nope"}},
                      {"default_classification": "x"}, {"other": 1}, []):
            status, body = self.admin("POST", "/api/v1/ui/admin/acl", {"rules": rules, "base": state["version"]})
            self.assertEqual((status, body.get("error")), (400, "invalid_rules"), rules)
            self.assertTrue(body["message"])
        self.assertEqual(self.acl_state()["version"], state["version"])

    def test_a_missing_or_unconfigured_rules_file(self):
        os.remove(self.rules_file)
        state = self.acl_state()
        self.assertEqual((state["exists"], state["version"], state["rules"]), (False, "none", {"paths": {}}))
        status, body = self.admin("POST", "/api/v1/ui/admin/acl", {"rules": {"paths": {}}, "base": "none"})
        self.assertEqual(status, 200, body)
        if os.name == "posix":
            self.assertEqual(os.stat(self.rules_file).st_mode & 0o777, 0o600)
        with patch.object(self.service, "ACL", acl.Policy()):
            state = self.acl_state()
            self.assertEqual(state["configured"], False)
            status, body = self.admin("POST", "/api/v1/ui/admin/acl", {"rules": {}, "base": "none"})
            self.assertEqual((status, body["error"]), (409, "rules_not_configured"))

    def test_a_broken_file_is_shown_and_can_be_replaced(self):
        with open(self.rules_file, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        state = self.acl_state()
        self.assertIsNone(state["rules"])
        self.assertIn("JSON", state["error"])
        status, _ = self.admin("POST", "/api/v1/ui/admin/acl", {"rules": {}, "base": state["version"]})
        self.assertEqual(status, 200)

    # -- events ------------------------------------------------------------------
    def test_events_newest_first_with_a_limit(self):
        for name in ("a", "b", "c"):
            self.admin("POST", "/api/v1/ui/admin/rooms/create", {"name": name})
        status, body = self.admin("GET", "/api/v1/ui/admin/events?limit=2")
        self.assertEqual([e["path"] for e in body["events"]], [PREFIX + "/c", PREFIX + "/b"])
        self.assertEqual(body["events"][0]["user"], "anna")


class AclAdminTest(unittest.TestCase):
    def test_without_tree(self):
        rules = {"paths": {ROOM: {}, ROOM + "/a": {}, ROOM + "x": {}}, "clearance": {}}
        self.assertEqual(acl_admin.without_tree(rules, ROOM),
                         {"paths": {ROOM + "x": {}}, "clearance": {}})

    def test_empty_nodes_are_dropped_and_allow_nobody_is_kept(self):
        rules = acl_admin.validate({"paths": {ROOM: {"inherit": True}, OTHER: {"allow": []}}}, PREFIX)
        self.assertEqual(rules, {"paths": {OTHER: {"allow": []}}})


class ClamdVersionTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(clamd.parse_version("ClamAV 1.4.6/28177/Sun Nov  8 06:23:52 2026"),
                         {"engine": "1.4.6", "database": 28177, "database_time": 1794119032})
        self.assertEqual(clamd.parse_version("ClamAV 1.0.0"),
                         {"engine": "1.0.0", "database": None, "database_time": None})
        self.assertEqual(clamd.parse_version("ClamAV 1.4.6/28177/garbage")["database_time"], None)
        self.assertEqual(clamd.parse_version("nonsense")["engine"], None)


if __name__ == "__main__":
    unittest.main()
