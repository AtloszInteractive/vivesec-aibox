"""The standalone file store (E27 S5): paths, quarantine, scan, release, index."""
import io
import json
import os
import sys
import tempfile
import unittest

import acl
import clamd
import drivestore
from drivestore import DriveStore, StoreError, UploadWorker

ROOM = "/storage/drives/cases"


class FakeScanner:
    def __init__(self, result=("clean", None), error=None):
        self.result = result
        self.error = error
        self.scanned = []

    def scan_file(self, path):
        with open(path, "rb") as handle:
            self.scanned.append(handle.read())
        if self.error is not None:
            raise self.error
        return self.result


class FakeIndex:
    def __init__(self, store):
        self.store = store
        self.indexed = []
        self.unindexed = []
        self.fail = None
        self.result = {"status": drivestore.INDEXED, "chunks": 3}

    def index(self, path):
        if self.fail is not None:
            raise self.fail
        self.indexed.append((path, self.store.read_bytes(path)))
        return dict(self.result)

    def unindex(self, path):
        self.unindexed.append(path)


class PathTest(unittest.TestCase):
    def test_canonical_paths(self):
        self.assertEqual(drivestore.canonical_path(ROOM + "/a/b.pdf"), ROOM + "/a/b.pdf")
        self.assertEqual(drivestore.canonical_path(ROOM + "/"), ROOM)
        # NFD (macOS) becomes NFC, so the same name cannot exist twice.
        self.assertEqual(drivestore.canonical_path(ROOM + "/a\u0301.pdf"), ROOM + "/\u00e1.pdf")
        self.assertEqual(drivestore.split_path(ROOM + "/x/y.txt"), ("cases", ["x", "y.txt"]))

    def test_invalid_paths(self):
        for path in ("/etc/passwd", "/storage/drives", "/storage/drives/", ROOM + "/../x.pdf",
                     ROOM + "/./x.pdf", ROOM + "//x.pdf", ROOM + "/.hidden", ROOM + "/a\\b.pdf",
                     ROOM + "/a\x00b.pdf", ROOM + "/a\nb.pdf", ROOM + "/x.pdf ",
                     ROOM + "/x.", ROOM + "/ x.pdf", ROOM + "/report\u202efdp.exe",
                     ROOM + "/" + "a" * 256, ROOM + "/" + "/".join(["d"] * 40) + "/x.txt",
                     "/storage/drivesX/a.pdf"):
            with self.assertRaises(StoreError, msg=repr(path)):
                drivestore.canonical_path(path)

    def test_settings(self):
        self.assertEqual(drivestore.parse_scan_mode(""), "required")
        self.assertEqual(drivestore.parse_scan_mode("ON"), "on")
        self.assertEqual(drivestore.parse_scan_mode("off"), "off")
        self.assertEqual(drivestore.parse_scan_mode("maybe", warn=lambda m: None), "required")
        self.assertIsNone(drivestore.parse_extensions("*"))
        self.assertEqual(drivestore.parse_extensions("pdf, .TXT"), frozenset({".pdf", ".txt"}))
        self.assertIn(".pdf", drivestore.parse_extensions(None))
        self.assertNotIn(".exe", drivestore.parse_extensions(""))
        self.assertEqual(drivestore.parse_writers(None), frozenset({"role:admin"}))
        self.assertEqual(drivestore.parse_writers("role:Admin, group:cases, bogus",
                                                  warn=lambda m: None),
                         frozenset({"role:admin", "group:cases"}))
        self.assertEqual(drivestore.parse_writers("bogus", warn=lambda m: None), frozenset())

    def test_may_write(self):
        admin = acl.Access(["user:anna", "role:admin"], 1, True)
        reader = acl.Access(["user:bob", "role:reader"], 1, True)
        writers = drivestore.parse_writers(None)
        self.assertTrue(drivestore.may_write(writers, admin))
        self.assertFalse(drivestore.may_write(writers, reader))
        self.assertFalse(drivestore.may_write(writers, None))
        self.assertTrue(drivestore.may_write(frozenset({"everyone"}), reader))
        self.assertFalse(drivestore.may_write(frozenset(), admin))


class StoreTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = os.path.join(self.temp.name, "files")
        self.store = DriveStore(self.root, max_bytes=1000)
        self.scanner = FakeScanner()
        self.index = FakeIndex(self.store)
        self.worker = UploadWorker(self.store, self.scanner, "required",
                                   self.index.index, self.index.unindex)

    def upload(self, path, data=b"hello world", overwrite=False, length=...):
        stream = io.BytesIO(data)
        record = self.store.receive(stream.read, len(data) if length is ... else length,
                                    "anna", ROOM, path, overwrite)
        self.worker.submit(record["id"])
        return record

    def events(self):
        with open(os.path.join(self.root, "events.jsonl"), encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]


class ReceiveTest(StoreTestBase):
    def test_upload_is_quarantined_with_its_hash(self):
        record = self.upload(ROOM + "/a/report.txt")
        self.assertEqual(record["status"], drivestore.QUEUED)
        self.assertEqual(record["size"], 11)
        self.assertEqual(record["sha256"],
                         "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9")
        self.assertIsNotNone(self.store.quarantined(record["id"]))
        self.assertIsNone(self.store.kind(ROOM + "/a/report.txt"))

    def test_size_limit_before_and_while_reading(self):
        with self.assertRaises(drivestore.UploadTooLarge):
            self.store.receive(io.BytesIO(b"").read, 1001, "anna", ROOM, ROOM + "/x.txt")
        with self.assertRaises(drivestore.UploadTooLarge):
            self.store.receive(io.BytesIO(b"x" * 1001).read, None, "anna", ROOM, ROOM + "/x.txt")
        with self.assertRaises(StoreError) as caught:
            self.store.receive(io.BytesIO(b"abc").read, 10, "anna", ROOM, ROOM + "/x.txt")
        self.assertEqual(caught.exception.code, "incomplete")
        self.assertEqual(os.listdir(os.path.join(self.root, "quarantine")), [])
        self.assertEqual(self.store.records(), [])


class WorkerTest(StoreTestBase):
    def test_clean_upload_is_stored_and_indexed(self):
        record = self.upload(ROOM + "/a/b/report.txt")
        self.worker.drain()
        done = self.store.record(record["id"])
        self.assertEqual(done["status"], drivestore.INDEXED)
        self.assertEqual(done["scan"], "clean")
        self.assertEqual(done["chunks"], 3)
        self.assertEqual(self.scanner.scanned, [b"hello world"])
        self.assertEqual(self.index.indexed, [(ROOM + "/a/b/report.txt", b"hello world")])
        self.assertIsNone(self.store.quarantined(record["id"]))
        self.assertEqual(self.store.kind(ROOM + "/a/b"), "folder")
        actions = [(e["action"], e.get("result")) for e in self.events()]
        self.assertEqual(actions, [("upload", None), ("scan", "clean"), ("store", "stored"),
                                   ("index", "indexed")])
        self.assertNotIn("hello", json.dumps(self.events()))

    def test_infected_upload_is_deleted_and_logged(self):
        self.scanner.result = ("infected", "Eicar-Signature")
        record = self.upload(ROOM + "/eicar.txt")
        self.worker.drain()
        done = self.store.record(record["id"])
        self.assertEqual(done["status"], drivestore.INFECTED)
        self.assertEqual(done["signature"], "Eicar-Signature")
        self.assertIsNone(self.store.quarantined(record["id"]))
        self.assertIsNone(self.store.kind(ROOM + "/eicar.txt"))
        self.assertEqual(self.index.indexed, [])
        scan = [e for e in self.events() if e["action"] == "scan"][0]
        self.assertEqual(scan["signature"], "Eicar-Signature")
        self.assertEqual(scan["sha256"], record["sha256"])

    def test_required_scan_waits_for_the_scanner(self):
        self.scanner.error = clamd.ScanUnavailable("down")
        record = self.upload(ROOM + "/a.txt")
        self.worker.drain()
        self.assertEqual(self.store.record(record["id"])["status"], drivestore.WAITING_SCANNER)
        self.assertIsNotNone(self.store.quarantined(record["id"]))
        self.assertIsNone(self.store.kind(ROOM + "/a.txt"))
        self.assertFalse(self.worker.scanner_state["ok"])
        self.scanner.error = None
        self.worker.resume()
        self.worker.drain()
        self.assertEqual(self.store.record(record["id"])["status"], drivestore.INDEXED)

    def test_scan_on_releases_when_the_scanner_is_down(self):
        self.worker.scan_mode = "on"
        self.scanner.error = clamd.ScanUnavailable("down")
        record = self.upload(ROOM + "/a.txt")
        self.worker.drain()
        done = self.store.record(record["id"])
        self.assertEqual(done["status"], drivestore.INDEXED)
        self.assertEqual(done["scan"], "unavailable")

    def test_scan_error_rejects_in_required_mode(self):
        self.scanner.error = clamd.ScanError("INSTREAM size limit exceeded. ERROR")
        record = self.upload(ROOM + "/a.txt")
        self.worker.drain()
        self.assertEqual(self.store.record(record["id"])["status"], drivestore.REJECTED)
        self.assertIsNone(self.store.quarantined(record["id"]))

    def test_scan_off_skips_the_scanner(self):
        self.worker.scan_mode = "off"
        record = self.upload(ROOM + "/a.txt")
        self.worker.drain()
        self.assertEqual(self.scanner.scanned, [])
        self.assertEqual(self.store.record(record["id"])["scan"], "off")

    def test_index_unavailable_is_retried(self):
        self.index.fail = OSError("rag down")
        record = self.upload(ROOM + "/a.txt")
        self.worker.drain()
        self.assertEqual(self.store.record(record["id"])["status"], drivestore.INDEX_PENDING)
        self.assertEqual(self.store.kind(ROOM + "/a.txt"), "file")
        self.index.fail = None
        self.worker.resume()
        self.worker.drain()
        self.assertEqual(self.store.record(record["id"])["status"], drivestore.INDEXED)
        self.assertEqual(self.scanner.scanned, [b"hello world"])

    def test_not_indexable_is_stored(self):
        self.index.result = {"status": drivestore.STORED, "reason": "too_large"}
        record = self.upload(ROOM + "/a.txt")
        self.worker.drain()
        done = self.store.record(record["id"])
        self.assertEqual((done["status"], done["reason"]), (drivestore.STORED, "too_large"))

    def test_existing_file_needs_overwrite(self):
        first = self.upload(ROOM + "/a.txt", b"one")
        second = self.upload(ROOM + "/a.txt", b"two")
        self.worker.drain()
        self.assertEqual(self.store.record(first["id"])["status"], drivestore.INDEXED)
        self.assertEqual(self.store.record(second["id"])["status"], drivestore.FAILED)
        self.assertEqual(self.store.record(second["id"])["reason"], "exists")
        third = self.upload(ROOM + "/a.txt", b"three", overwrite=True)
        self.worker.drain()
        self.assertEqual(self.store.record(third["id"])["status"], drivestore.INDEXED)
        self.assertEqual(self.store.read_bytes(ROOM + "/a.txt"), b"three")

    def test_folder_and_file_conflicts(self):
        self.upload(ROOM + "/a.txt")
        self.worker.drain()
        below_file = self.upload(ROOM + "/a.txt/b.txt")
        self.store.make_dirs(ROOM + "/folder")
        onto_folder = self.upload(ROOM + "/folder", overwrite=True)
        self.worker.drain()
        self.assertEqual(self.store.record(below_file["id"])["reason"], "parent_is_file")
        self.assertEqual(self.store.record(onto_folder["id"])["reason"], "is_folder")
        self.assertEqual(self.store.conflict(ROOM + "/a.txt/c.txt"), "parent_is_file")
        with self.assertRaises(StoreError):
            self.store.make_dirs(ROOM + "/a.txt/sub")

    def test_delete_cancels_pending_uploads(self):
        self.scanner.error = clamd.ScanUnavailable("down")
        record = self.upload(ROOM + "/a/b.txt")
        self.worker.drain()
        self.assertEqual(self.store.cancel_under(ROOM + "/a", "anna"), [record["id"]])
        self.assertEqual(self.store.record(record["id"])["status"], drivestore.CANCELLED)
        self.assertIsNone(self.store.quarantined(record["id"]))
        self.scanner.error = None
        self.worker.resume()
        self.worker.drain()
        self.assertIsNone(self.store.kind(ROOM + "/a/b.txt"))

    def test_deleted_while_indexing_is_unindexed(self):
        def index(path):
            self.store.cancel_under(path, "bob")
            self.store.remove(path)
            return {"status": drivestore.INDEXED}

        self.worker.index = index
        record = self.upload(ROOM + "/a.txt")
        self.worker.drain()
        self.assertEqual(self.store.record(record["id"])["status"], drivestore.CANCELLED)
        self.assertEqual(self.index.unindexed, [ROOM + "/a.txt"])

    def test_dismiss(self):
        self.scanner.error = clamd.ScanUnavailable("down")
        record = self.upload(ROOM + "/a.txt")
        self.worker.drain()
        self.assertFalse(self.store.dismiss(record["id"], "bob"))
        self.assertTrue(self.store.dismiss(record["id"], "anna"))
        self.assertIsNone(self.store.record(record["id"]))
        self.assertIsNone(self.store.quarantined(record["id"]))
        self.index.fail = OSError("down")
        self.scanner.error = None
        pending = self.upload(ROOM + "/b.txt")
        self.worker.drain()
        with self.assertRaises(StoreError):
            self.store.dismiss(pending["id"], "anna")

    def test_records_survive_a_restart(self):
        self.scanner.error = clamd.ScanUnavailable("down")
        record = self.upload(ROOM + "/a.txt")
        self.worker.drain()
        store = DriveStore(self.root, max_bytes=1000)
        self.assertEqual(store.record(record["id"])["status"], drivestore.WAITING_SCANNER)
        index = FakeIndex(store)
        worker = UploadWorker(store, FakeScanner(), "required", index.index, index.unindex)
        worker.resume()
        worker.drain()
        self.assertEqual(store.record(record["id"])["status"], drivestore.INDEXED)


class DiskTest(StoreTestBase):
    def test_listing_walk_remove(self):
        for path in (ROOM + "/a/one.txt", ROOM + "/a/b/two.txt", ROOM + "/three.txt"):
            self.upload(path)
        self.worker.drain()
        root = self.store.list_dir(ROOM)
        self.assertEqual([(e["name"], e["file"]) for e in root], [("a", False), ("three.txt", True)])
        self.assertEqual(root[1]["size"], 11)
        self.assertEqual(sorted(self.store.walk(ROOM + "/a")),
                         [ROOM + "/a", ROOM + "/a/b", ROOM + "/a/b/two.txt", ROOM + "/a/one.txt"])
        self.assertEqual(self.store.list_dir(ROOM + "/missing"), [])
        self.assertTrue(self.store.remove(ROOM + "/a"))
        self.assertIsNone(self.store.kind(ROOM + "/a"))
        with self.assertRaises(StoreError):
            self.store.remove(ROOM)

    def test_make_dirs_reports_created_folders(self):
        self.assertEqual(self.store.make_dirs(ROOM + "/x/y"), [ROOM, ROOM + "/x", ROOM + "/x/y"])
        self.assertEqual(self.store.make_dirs(ROOM + "/x/y/z"), [ROOM + "/x/y/z"])

    def test_invalid_names_on_disk_are_not_listed(self):
        self.store.make_dirs(ROOM)
        local = os.path.join(self.root, "rooms", "cases")
        for name in (".hidden", "ok.txt"):
            with open(os.path.join(local, name), "w") as handle:
                handle.write("x")
        self.assertEqual([e["name"] for e in self.store.list_dir(ROOM)], ["ok.txt"])

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges on Windows")
    def test_symlinks_are_not_followed(self):
        self.store.make_dirs(ROOM)
        outside = os.path.join(self.temp.name, "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, "secret.txt"), "w") as handle:
            handle.write("secret")
        os.symlink(outside, os.path.join(self.root, "rooms", "cases", "link"))
        os.symlink(os.path.join(outside, "secret.txt"),
                   os.path.join(self.root, "rooms", "cases", "file.txt"))
        self.assertEqual(self.store.list_dir(ROOM), [])
        for path in (ROOM + "/link/secret.txt", ROOM + "/file.txt"):
            with self.assertRaises(StoreError, msg=path):
                self.store.read_bytes(path)
        with self.assertRaises(StoreError):
            self.store.kind(ROOM + "/link")

    def test_purge(self):
        self.upload(ROOM + "/a.txt")
        self.worker.drain()
        self.assertEqual(self.store.purge(), 1)
        self.assertEqual(self.store.records(), [])
        self.assertIsNone(self.store.kind(ROOM + "/a.txt"))
        self.upload(ROOM + "/b.txt")
        self.worker.drain()
        self.assertEqual(self.store.kind(ROOM + "/b.txt"), "file")

    def test_disabled_store(self):
        store = DriveStore(None)
        self.assertFalse(store.enabled)
        with self.assertRaises(StoreError) as caught:
            store.receive(io.BytesIO(b"x").read, 1, "anna", ROOM, ROOM + "/a.txt")
        self.assertEqual(caught.exception.status, 503)


if __name__ == "__main__":
    unittest.main()
