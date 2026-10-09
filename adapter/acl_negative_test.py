"""E03 negative suite: nothing a caller may not see leaks through any channel.

End to end: a real rag_service (subprocess, deterministic fallback embeddings)
behind the real adapter handler; only the language model is replaced by an
echo of its context, so whatever reaches the model is visible in the answer.
Every check is paired with a positive control (a caller who MAY see the
document does see it), otherwise "nothing found" would prove nothing.

Covered: indirect summary, plain and file-naming questions, #analyze, filename
search, folder listing, download, per-user statistics and hit counts, the
status counters, unknown group membership, classification, background jobs, an
ACL-only change and a stale RAG copy of the ACL.

This runs mocked generation over a tiny corpus: it proves the filtering, not
answer quality or large-corpus latency.
"""
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import acl
import directory
import jobstore
import llm
from meta import MetaMirror

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVE = "/storage/drives/finance"
HR_DRIVE = "/storage/drives/hr"

DOCS = {
    DRIVE + "/travel.txt": "The travel policy grants a daily allowance for every trip.",
    DRIVE + "/hr/layoffs-2027.txt": "The layoff plan names Zebulon Quartz as the first redundancy.",
    DRIVE + "/board/merger.txt": "The board approved the merger with Quillfeather Holdings.",
    DRIVE + "/shared/contractors-note.txt": "The contractor rate card lists Vexmoor hourly fees.",
    HR_DRIVE + "/handbook.txt": "The employee handbook explains the Plumtree leave rules.",
}
SECRETS = ("Zebulon", "layoffs-2027", "Quillfeather", "merger.txt")

DIRECTORY_DATA = {
    "lars": ([], []),
    "hanna": (["hr"], []),
    "eve": (["contractors"], []),
    "boss": ([], ["board"]),
}


def b64(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeDirectory:
    source = "token"

    def lookup(self, identity):
        user = identity.user if identity is not None else ""
        if user not in DIRECTORY_DATA:
            return directory.DirectoryInfo("token", error="directory unreachable")
        groups, roles = DIRECTORY_DATA[user]
        return directory.DirectoryInfo("token", groups, roles)

    def settings(self):
        return {"source": "token"}


def echo_model(query, contexts, **_kw):
    if not contexts:
        return "NO-CONTEXT", "echo"
    return "\n".join(c.get("text") or "" for c in contexts), "echo"


class AclNegativeSqliteTest(unittest.TestCase):
    BACKEND = "sqlite"

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        port = free_port()
        env = dict(os.environ, VIVESEC_BACKEND="fallback", RAG_STORE_BACKEND=cls.BACKEND,
                   RAG_PORT=str(port), RAG_HOST="127.0.0.1", RAG_API_KEY="",
                   RAG_INDEX_PATH=os.path.join(cls.temp.name, "index." + cls.BACKEND))
        cls.rag = subprocess.Popen([sys.executable, os.path.join(REPO, "rag_service", "service.py")],
                                   env=env, cwd=REPO, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        cls.rag_url = "http://127.0.0.1:%d" % port
        deadline = time.time() + 60
        while True:
            try:
                urllib.request.urlopen(cls.rag_url + "/ready", timeout=2).read()
                break
            except Exception:  # noqa: BLE001
                if time.time() > deadline or cls.rag.poll() is not None:
                    cls.rag.kill()
                    raise RuntimeError("rag_service did not start (%s backend)" % cls.BACKEND)
                time.sleep(0.2)
        with patch.dict(os.environ, {
            "ADAPTER_PKI_DIR": cls.temp.name + "/pki",
            "ADAPTER_FILES_DIR": cls.temp.name + "/files",
            "ADAPTER_FEEDBACK_DIR": cls.temp.name + "/feedback",
            "ADAPTER_JOBS_DIR": cls.temp.name + "/jobs",
            "ADAPTER_SESSION_DIR": cls.temp.name + "/sessions",
            "ADAPTER_STORAGE_MODE": "off",
            "ADAPTER_META_PATH": "",
        }):
            import service
        cls.service = service

    @classmethod
    def tearDownClass(cls):
        cls.rag.terminate()
        try:
            cls.rag.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.rag.kill()
        cls.temp.cleanup()

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.rules = os.path.join(self.dir.name, "acl.json")
        self.write_rules({"clearance": {"role:board": "strictly_confidential"}})
        self.mirror = MetaMirror()
        self.policy = acl.Policy(rules_path=self.rules)
        service = self.service
        patches = {"RAG_URL": self.rag_url, "MIRROR": self.mirror, "ACL": self.policy,
                   "DIRECTORY": FakeDirectory(),
                   "JOBS": jobstore.JobStore(os.path.join(self.dir.name, "jobs")),
                   "HTTP_SCOPE": service.HTTP_SCOPE_FULL,
                   "ENTITLEMENTS": None, "_ACL_SYNC": {"fingerprint": None, "generation": 0}}
        for name, value in patches.items():
            patcher = patch.object(service, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (("generate", echo_model), ("TITLES", False),
                            ("condense", lambda q, h, lang=None: (q, False))):
            patcher = patch.object(llm, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.call("/api/v1/index/drop/tree", {"path": DRIVE})
        self.call("/api/v1/index/drop/tree", {"path": HR_DRIVE})
        self.sync_corpus()

    # -- helpers -----------------------------------------------------------------
    def write_rules(self, data):
        with open(self.rules, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        stamp = time.time() + (getattr(self, "_bump", 0))
        self._bump = getattr(self, "_bump", 0) + 2
        os.utime(self.rules, (stamp + self._bump, stamp + self._bump))

    def call(self, path, body=None, user=None, method=None, extra=None, raw=None):
        headers = {"Content-Type": "application/json"}
        if user:
            headers.update({"VVS-Drive": b64(DRIVE + "/"), "VVS-User": user,
                            "VVS-Other-Drives": b64(HR_DRIVE)})
        headers.update(extra or {})
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.server.server_port, path), data=data,
            method=method or ("POST" if data is not None else "GET"), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status, payload = response.status, response.read()
        except urllib.error.HTTPError as e:
            status, payload = e.code, e.read()
        try:
            return status, json.loads(payload.decode("utf-8"))
        except ValueError:
            return status, payload.decode("utf-8", "replace")

    def sync_file(self, path, text, node_acl=None, with_acl=False):
        body = {"path": path, "size": len(text.encode()), "mtime": 1700000000,
                "head": base64.b64encode(text.encode()[:64]).decode()}
        if with_acl:
            body["acl"] = node_acl
        status, check = self.call("/api/v1/index/upsert/file/check", body)
        self.assertEqual(status, 200, check)
        self.assertTrue(check.get("token"), check)
        status, done = self.call("/api/v1/index/upsert/file/content/" + check["token"],
                                 raw=text.encode(), extra={"Content-Type": "text/plain"})
        self.assertEqual(status, 200, done)

    def sync_dir(self, path, node_acl=None, with_acl=False):
        body = {"path": path}
        if with_acl:
            body["acl"] = node_acl
        status, out = self.call("/api/v1/index/upsert/directory", body)
        self.assertEqual(status, 200, out)

    def sync_corpus(self):
        for root in (DRIVE, HR_DRIVE):
            self.sync_dir(root)
        self.sync_dir(DRIVE + "/hr", {"allow": ["group:hr"]}, with_acl=True)
        self.sync_dir(DRIVE + "/board", {"classification": "strictly_confidential"}, with_acl=True)
        self.sync_dir(DRIVE + "/shared")
        for path, text in DOCS.items():
            node = {"deny": ["group:contractors"]} if path.endswith("contractors-note.txt") else None
            self.sync_file(path, text, node, with_acl=node is not None)

    def ask(self, user, query, **body):
        status, out = self.call("/api/v1/ui/query", dict(body, query=query), user=user)
        self.assertEqual(status, 200, out)
        return out

    def assert_no_secret(self, payload, secrets=SECRETS):
        blob = json.dumps(payload)
        for secret in secrets:
            self.assertNotIn(secret, blob)

    def hit_paths(self, out):
        return {hit["path"] for hit in out.get("hits") or []}

    # -- retrieval -----------------------------------------------------------------
    def test_indirect_summary_does_not_leak(self):
        out = self.ask("lars", "#summary everything about layoffs, redundancy, Zebulon and mergers")
        self.assert_no_secret(out)
        control = self.ask("hanna", "#summary everything about layoffs, redundancy and Zebulon")
        self.assertIn(DRIVE + "/hr/layoffs-2027.txt", self.hit_paths(control))

    def test_naming_a_hidden_file_finds_nothing(self):
        out = self.ask("lars", "What does layoffs-2027.txt say about Zebulon Quartz?")
        self.assertNotIn(DRIVE + "/hr/layoffs-2027.txt", self.hit_paths(out))
        self.assertNotIn("Zebulon", out["answer"])
        out = self.ask("lars", "summarize", action="summary",
                       files=[DRIVE + "/hr/layoffs-2027.txt"])
        self.assertEqual(out["hits"], [])

    def test_analyze_of_a_hidden_file_looks_like_a_missing_file(self):
        hidden = self.ask("lars", "analyse", action="analyze",
                          files=[DRIVE + "/hr/layoffs-2027.txt"])
        missing = self.ask("lars", "analyse", action="analyze",
                           files=[DRIVE + "/hr/no-such-file.txt"])
        self.assert_no_secret(hidden)
        self.assertEqual(hidden["hits"], missing["hits"])
        self.assertEqual(hidden["document"], missing["document"])
        control = self.ask("hanna", "analyse", action="analyze",
                           files=[DRIVE + "/hr/layoffs-2027.txt"])
        self.assertIn(DRIVE + "/hr/layoffs-2027.txt", self.hit_paths(control))

    def test_hit_count_covers_only_visible_chunks(self):
        lars = self.ask("lars", "policy plan rules rate approved")
        hanna = self.ask("hanna", "policy plan rules rate approved")
        boss = self.ask("boss", "policy plan rules rate approved")
        count = lambda out: out["retrieval_debug"]["chunk_hits_count"]  # noqa: E731
        self.assertEqual(count(lars), 3)   # travel, contractors note, handbook
        self.assertEqual(count(hanna), 4)  # + layoffs
        self.assertEqual(count(boss), 4)   # + merger, - layoffs
        self.assert_no_secret(lars)

    def test_classification_follows_the_clearance(self):
        out = self.ask("lars", "board merger Quillfeather Holdings")
        self.assertNotIn(DRIVE + "/board/merger.txt", self.hit_paths(out))
        self.assertNotIn("Quillfeather", out["answer"])
        control = self.ask("boss", "board merger Quillfeather Holdings")
        self.assertIn(DRIVE + "/board/merger.txt", self.hit_paths(control))

    def test_unknown_membership_hides_group_denied_documents(self):
        note = DRIVE + "/shared/contractors-note.txt"
        self.assertNotIn(note, self.hit_paths(self.ask("eve", "contractor rate card Vexmoor")))
        self.assertNotIn(note, self.hit_paths(self.ask("mallory", "contractor rate card Vexmoor")))
        self.assertIn(note, self.hit_paths(self.ask("lars", "contractor rate card Vexmoor")))

    def test_background_job_runs_with_the_submitters_access(self):
        status, job = self.call("/api/v1/ui/ask", {"query": "layoff plan Zebulon Quartz"},
                                user="lars")
        self.assertEqual(status, 202, job)
        status, out = self.call("/api/v1/ui/poll", {"job_id": job["job_id"], "timeout": 30},
                                user="lars")
        self.assertEqual(status, 200, out)
        self.assert_no_secret(out)

    # -- file names, listing, download ---------------------------------------------
    def test_filename_search_and_counts(self):
        out = self.ask("lars", "#search files: layoffs")
        self.assertEqual(out["files"], [])
        everything = self.ask("lars", "#search files:")
        self.assertEqual(len(everything["files"]), 3)
        self.assert_no_secret(everything)
        control = self.ask("hanna", "#search files: layoffs")
        self.assertEqual([f["path"] for f in control["files"]], [DRIVE + "/hr/layoffs-2027.txt"])

    def test_folder_listing_hides_folders_and_files(self):
        status, out = self.call("/api/v1/ui/files/children", {"path": DRIVE}, user="lars")
        self.assertEqual(status, 200, out)
        names = {entry["path"] for entry in out["entries"]}
        self.assertNotIn(DRIVE + "/hr", names)
        self.assertNotIn(DRIVE + "/board", names)
        self.assertIn(DRIVE + "/travel.txt", names)
        hidden = self.call("/api/v1/ui/files/children", {"path": DRIVE + "/hr"}, user="lars")
        missing = self.call("/api/v1/ui/files/children", {"path": DRIVE + "/nope"}, user="lars")
        self.assertEqual(hidden[0], 404)
        self.assertEqual(hidden[1], {"ok": False, "error": "not-found"})
        self.assertIn(missing[0], (200, 404))
        status, other = self.call("/api/v1/ui/files/children",
                                  {"path": "/storage/drives/legal"}, user="lars")
        self.assertEqual(status, 403)
        status, control = self.call("/api/v1/ui/files/children", {"path": DRIVE + "/hr"},
                                    user="hanna")
        self.assertEqual(status, 200)
        self.assertEqual([e["path"] for e in control["entries"]], [DRIVE + "/hr/layoffs-2027.txt"])

    def test_download_of_a_hidden_file_never_reaches_the_box(self):
        with patch.object(self.service.WSFS, "get_file") as get_file:
            status, out = self.call("/api/v1/ui/file", {"path": DRIVE + "/hr/layoffs-2027.txt"},
                                    user="lars")
        self.assertEqual((status, out), (404, {"ok": False, "error": "not-found"}))
        get_file.assert_not_called()
        with patch.object(self.service.WSFS, "get_file",
                          return_value=({}, b"content")) as get_file:
            status, _ = self.call("/api/v1/ui/file", {"path": DRIVE + "/hr/layoffs-2027.txt"},
                                  user="hanna")
        self.assertEqual(status, 200)

    def test_dot_segments_cannot_dodge_a_folder_acl(self):
        with patch.object(self.service.WSFS, "get_file") as get_file:
            for path in (DRIVE + "/./hr/layoffs-2027.txt", DRIVE + "//hr/layoffs-2027.txt",
                         DRIVE + "/hr/./layoffs-2027.txt"):
                status, _ = self.call("/api/v1/ui/file", {"path": path}, user="lars")
                self.assertIn(status, (403, 404), path)
                status, _ = self.call("/api/v1/ui/files/children",
                                      {"path": path.rsplit("/", 1)[0]}, user="lars")
                self.assertIn(status, (403, 404), path)
        get_file.assert_not_called()

    # -- statistics ------------------------------------------------------------------
    def test_insight_counts_only_what_the_caller_sees(self):
        status, lars = self.call("/api/v1/ui/insight", user="lars")
        self.assertEqual(status, 200, lars)
        status, hanna = self.call("/api/v1/ui/insight", user="hanna")
        self.assertEqual(lars["files"], 3)
        self.assertEqual(hanna["files"], 4)
        self.assertEqual(lars["index"]["documents"], 3)
        self.assertEqual(hanna["index"]["documents"], 4)
        self.assertEqual(lars["folders"], 1)  # shared/ only
        self.assert_no_secret(lars)

    def test_status_counters_only_on_the_trusted_channel(self):
        status, relayed = self.call("/api/v1/status", extra={"X-Forwarded-Host": "ui"})
        self.assertEqual(status, 200)
        self.assertNotIn("index", relayed)
        self.assertNotIn("mirror", relayed)
        status, local = self.call("/api/v1/status")
        self.assertIn("index", local)

    def test_scope_hides_a_denied_room(self):
        self.write_rules({"paths": {HR_DRIVE: {"deny": ["user:lars"]}},
                          "clearance": {"role:board": "strictly_confidential"}})
        status, out = self.call("/api/v1/ui/scope", user="lars")
        self.assertEqual(status, 200)
        self.assertEqual([d["path"] for d in out["drives"]], [DRIVE])
        self.assertNotIn(HR_DRIVE + "/handbook.txt",
                         self.hit_paths(self.ask("lars", "employee handbook Plumtree leave")))

    # -- changes ---------------------------------------------------------------------
    def test_acl_only_change_reaches_the_index(self):
        status, out = self.call("/api/v1/index/upsert/acl",
                                {"path": DRIVE + "/hr", "acl": {"allow": ["group:hr", "user:lars"]}})
        self.assertEqual((status, out.get("changed")), (200, True), out)
        granted = self.ask("lars", "layoff plan Zebulon Quartz redundancy")
        self.assertIn(DRIVE + "/hr/layoffs-2027.txt", self.hit_paths(granted))
        self.call("/api/v1/index/upsert/acl", {"path": DRIVE + "/hr", "acl": {"allow": ["group:hr"]}})
        self.assert_no_secret(self.ask("lars", "layoff plan Zebulon Quartz redundancy"))

    def lars_index_documents(self):
        status, out = self.call("/api/v1/ui/insight", user="lars")
        self.assertEqual(status, 200, out)
        return out["index"]["documents"]

    def test_acl_change_between_check_and_upload_is_not_overwritten(self):
        self.call("/api/v1/index/upsert/acl",
                  {"path": DRIVE + "/hr", "acl": {"allow": ["group:hr", "user:lars"]}})
        text = "The late memo mentions Xylograph."
        path = DRIVE + "/hr/late.txt"
        status, check = self.call("/api/v1/index/upsert/file/check", {
            "path": path, "size": len(text), "mtime": 1700000001,
            "head": base64.b64encode(text.encode()).decode()})
        self.assertTrue(check.get("token"), check)
        # Revoked while the content is still on its way.
        self.call("/api/v1/index/upsert/acl", {"path": DRIVE + "/hr", "acl": {"allow": ["group:hr"]}})
        status, _ = self.call("/api/v1/index/upsert/file/content/" + check["token"],
                              raw=text.encode(), extra={"Content-Type": "text/plain"})
        self.assertEqual(status, 200)
        self.assertEqual(self.lars_index_documents(), 3)

    def test_failed_directory_sync_still_reaches_the_index(self):
        real = self.service.rag_post_json

        def failing(path, body):
            if path == "/index/upsert/directory":
                raise self.service.RagError(503, {"ok": False, "error": "rag restarting"})
            return real(path, body)

        self.service._acl_resync()
        self.assertIsNotNone(self.service._ACL_SYNC["fingerprint"])
        self.assertEqual(self.lars_index_documents(), 3)
        with patch.object(self.service, "rag_post_json", side_effect=failing):
            status, _ = self.call("/api/v1/index/upsert/directory",
                                  {"path": DRIVE + "/hr", "acl": {"allow": ["everyone"]}})
        self.assertEqual(status, 503)
        # The box retries: the mirror already holds the ACL, nothing "changed".
        self.call("/api/v1/index/upsert/directory",
                  {"path": DRIVE + "/hr", "acl": {"allow": ["everyone"]}})
        self.assertIsNone(self.service._ACL_SYNC["fingerprint"])
        self.service._acl_resync()
        self.assertEqual(self.lars_index_documents(), 4)

    def test_resync_overlapping_an_incremental_push_stays_incomplete(self):
        real = self.service._push_acl

        def racing(paths):
            updated = real(paths)
            with self.service._ACL_GENERATION_LOCK:
                self.service._ACL_SYNC["generation"] += 1
            return updated

        with patch.object(self.service, "_push_acl", side_effect=racing):
            self.service._acl_resync()
        self.assertIsNone(self.service._ACL_SYNC["fingerprint"])
        self.service._acl_resync()
        self.assertIsNotNone(self.service._ACL_SYNC["fingerprint"])

    def test_malformed_sync_acl_hides_the_subtree(self):
        self.call("/api/v1/index/upsert/acl", {"path": DRIVE + "/shared", "acl": {"allow": "x"}})
        out = self.ask("lars", "contractor rate card Vexmoor")
        self.assertNotIn(DRIVE + "/shared/contractors-note.txt", self.hit_paths(out))

    def test_stale_rag_acl_is_caught_by_the_adapter(self):
        # The rules file now hides travel.txt, but the RAG has not been told.
        self.write_rules({"paths": {DRIVE + "/travel.txt": {"deny": ["user:lars"]}},
                          "clearance": {"role:board": "strictly_confidential"}})
        out = self.ask("lars", "travel policy daily allowance")
        self.assertNotIn(DRIVE + "/travel.txt", self.hit_paths(out))
        self.assertNotIn("daily allowance", out["answer"])
        self.service._acl_resync()
        out = self.ask("lars", "travel policy daily allowance")
        self.assertNotIn(DRIVE + "/travel.txt", self.hit_paths(out))
        self.assertEqual(out["retrieval_debug"]["chunk_hits_count"], 2)

    # -- admin ---------------------------------------------------------------------
    def test_effective_permission_view(self):
        status, out = self.call("/api/v1/admin/acl/effective", {
            "user": "lars", "paths": [DRIVE + "/hr/layoffs-2027.txt", DRIVE + "/travel.txt"],
            "under": DRIVE})
        self.assertEqual(status, 200, out)
        self.assertEqual([p["allowed"] for p in out["paths"]], [False, True])
        self.assertEqual(out["paths"][0]["reason"], "not in allow list")
        self.assertEqual(out["under"], {"path": DRIVE, "files": 4, "visible": 2, "hidden": 2})
        status, what_if = self.call("/api/v1/admin/acl/effective", {
            "user": "lars", "groups": ["hr"], "paths": [DRIVE + "/hr/layoffs-2027.txt"]})
        self.assertTrue(what_if["paths"][0]["allowed"])
        status, _ = self.call("/api/v1/admin/acl/effective", {"user": "lars"},
                              extra={"X-Forwarded-Host": "ui"})
        self.assertEqual(status, 403)


class AclNegativeJsonTest(AclNegativeSqliteTest):
    BACKEND = "json"


if __name__ == "__main__":
    unittest.main()
