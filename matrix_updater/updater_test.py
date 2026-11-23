import datetime as dt
import functools
import json
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts" / "release"))

import updater as up  # noqa: E402

try:
    import tuf  # noqa: F401
    import matrix_repo as repo_tool  # noqa: E402
except ImportError:  # python-tuf is a host dependency; the stdlib-only CI skips these
    repo_tool = None

ADAPTER_ID = "sha256:" + "a" * 64
GRAPH_ID = "sha256:" + "b" * 64
OLD_ADAPTER = "sha256:" + "1" * 64
OLD_GRAPH = "sha256:" + "2" * 64
PASSPHRASE = "correct horse battery staple"


class FakeHost:
    """docker, cp and the installer as the updater sees them."""

    def __init__(self, images, codes=None):
        self.images = dict(images)
        self.codes = list(codes or [])
        self.commands, self.installers = [], []
        self.log_path = None
        self.on_install = None

    def run(self, args, timeout=None):
        args = [str(arg) for arg in args]
        if args[0] == "python3":
            kit = Path(args[1]).parents[3]
            self.installers.append(json.loads((kit / "KIT.json").read_text())["label"])
            if self.on_install and len(self.installers) == 1:
                self.on_install()
            return self.codes.pop(0) if self.codes else 0
        self.commands.append(args)
        if args[:2] == ["docker", "load"]:
            name, image_id = Path(args[3]).read_text().split()[:2]
            self.images[image_id] = image_id
        elif args[:2] == ["docker", "tag"]:
            self.images[args[3]] = self.images.get(args[2], args[2])
        elif args[0] == "cp":
            shutil.copytree(args[3], args[4])
        return 0

    def image_id(self, ref):
        return self.images.get(ref)


class Handler(SimpleHTTPRequestHandler):
    agents = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        Handler.agents.append(self.headers.get("User-Agent", ""))
        super().do_GET()


@unittest.skipIf(repo_tool is None, "python-tuf not installed")
class UpdateChainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.repo, self.keys = base / "repo", base / "keys"
        trusted = repo_tool.init(self.repo, self.keys, passphrase=PASSPHRASE)
        self.kit = base / "kit"
        (self.kit / "src" / "matrix_updater").mkdir(parents=True)
        (self.kit / "src" / "matrix_updater" / "trusted_root.json").write_bytes(trusted.read_bytes())
        self.set_installed("26.10.1-dev+4d28a33")
        handler = functools.partial(Handler, directory=str(self.repo))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.server.server_port
        self.base = base
        Handler.agents = []

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def set_installed(self, label):
        (self.kit / "KIT.json").write_text(json.dumps({"label": label}))

    def release(self, version, min_from=None, images=(("vivesec-adapter", ADAPTER_ID), ("vivesec-graph", GRAPH_ID)),
                channel="stable", snapshot=False):
        out = self.base / ("release-" + version)
        (out / "images").mkdir(parents=True, exist_ok=True)
        kit_src = out / "kit-src"
        (kit_src / "src" / "scripts" / "spark").mkdir(parents=True, exist_ok=True)
        (kit_src / "src" / "scripts" / "spark" / "spark_install.py").write_text("# installer\n")
        (kit_src / "src" / "matrix_updater").mkdir(parents=True, exist_ok=True)
        (kit_src / "src" / "matrix_updater" / "trusted_root.json").write_bytes(
            (self.repo / "trusted_root.json").read_bytes())
        (kit_src / "KIT.json").write_text(json.dumps({"label": version, "images": [
            {"name": name, "id": image_id} for name, image_id in images]}))
        with tarfile.open(out / "matrix-kit.tgz", "w:gz") as archive:
            archive.add(kit_src / "KIT.json", "KIT.json")
            archive.add(kit_src / "src", "src")
        entries = []
        for name, image_id in images:
            (out / "images" / (name + ".tar.zst")).write_bytes(("%s %s %s\n" % (name, image_id, version)).encode() * 100)
            entries.append({"name": name, "id": image_id, "file": "images/%s.tar.zst" % name})
        (out / "release.json").write_text(json.dumps({
            "format": 1, "product": "matrix", "version": version, "label": version, "commit": "c" * 40,
            "min_from": min_from, "security": False, "notes": {"hu": "Javítások", "en": "Fixes"},
            "compat": {"snapshot": snapshot, "reindex": False}, "kit": {"file": "matrix-kit.tgz"},
            "images": entries}))
        return repo_tool.publish(self.repo, self.keys, channel, out, PASSPHRASE)

    def settings(self, state="state", **values):
        values = {"MATRIX_UPDATE": "on", "MATRIX_UPDATE_URL": self.url, **values}
        return up.Settings(values, state_dir=self.base / state, share_dir=self.base / (state + "-share"),
                           kit_dir=self.kit, allow_http=True, root=self.base / "data")

    def request(self, settings, host, **request):
        (settings.share_dir / "request.json").write_text(json.dumps(dict(request, by="admin")))
        return up.handle_request(settings, host)

    def test_an_approved_release_is_installed(self):
        self.release("26.11.1")
        settings = self.settings()
        host = FakeHost({"vivesec-adapter:latest": OLD_ADAPTER, "vivesec-graph:latest": OLD_GRAPH})
        self.assertEqual(up.check(settings, has_image=lambda _: False)["status"], "ready")
        state = self.request(settings, host, action="install", version="26.11.1")
        self.assertEqual((state["status"], state["installed"]), ("current", "26.11.1"), state.get("error"))
        self.assertEqual(host.installers, ["26.11.1"])
        loaded = [c[3] for c in host.commands if c[:2] == ["docker", "load"]]
        self.assertEqual([any(name in path for path in loaded) for name in ("vivesec-adapter", "vivesec-graph")],
                         [True, True])
        self.assertEqual(json.loads((self.base / "kit.prev" / "KIT.json").read_text())["label"],
                         "26.10.1-dev+4d28a33")
        self.assertEqual(state["last"]["previous"]["images"]["vivesec-adapter"], OLD_ADAPTER)
        self.assertFalse((settings.share_dir / "request.json").exists())
        self.assertEqual(up.check(settings, has_image=lambda _: True)["status"], "current")

    def test_a_failing_install_brings_the_previous_version_back(self):
        self.release("26.11.1")
        settings = self.settings()
        host = FakeHost({"vivesec-adapter:latest": OLD_ADAPTER, "vivesec-graph:latest": OLD_GRAPH}, codes=[1, 0])
        up.check(settings, has_image=lambda _: False)
        state = self.request(settings, host, action="install", version="26.11.1")
        self.assertEqual(state["status"], "failed")
        self.assertEqual(host.installers, ["26.11.1", "26.10.1-dev+4d28a33"])
        self.assertEqual((host.images["vivesec-adapter:latest"], host.images["vivesec-graph:latest"]),
                         (OLD_ADAPTER, OLD_GRAPH))
        self.assertEqual(state["installed"], "26.10.1-dev+4d28a33")
        self.assertEqual(state["available"]["version"], "26.11.1")  # may be approved again

    def test_a_format_change_copies_the_data_and_a_rollback_can_restore_it(self):
        self.release("26.11.1", snapshot=True)
        data = self.base / "data" / "adapter"
        data.mkdir(parents=True)
        (data / "meta.json").write_text("before")
        settings = self.settings()
        host = FakeHost({})
        up.check(settings, has_image=lambda _: False)
        state = self.request(settings, host, action="install", version="26.11.1")
        self.assertEqual(state["status"], "current", state.get("error"))
        self.assertTrue(any(c[:2] == ["docker", "stop"] for c in host.commands))
        (data / "meta.json").write_text("after")
        state = self.request(settings, host, action="rollback", restore_data=True)
        self.assertEqual(state["status"], "current", state.get("error"))
        self.assertEqual((data / "meta.json").read_text(), "before")
        self.assertEqual(Path(state["last"]["replaced_data"], "adapter", "meta.json").read_text(), "after")
        self.assertEqual(state["installed"], "26.10.1-dev+4d28a33")

    def test_a_failing_install_after_a_data_copy_puts_the_data_back(self):
        self.release("26.11.1", snapshot=True)
        data = self.base / "data" / "adapter"
        data.mkdir(parents=True)
        (data / "meta.json").write_text("before")
        settings = self.settings()
        host = FakeHost({}, codes=[1, 0])
        host.on_install = lambda: (data / "meta.json").write_text("migrated")
        up.check(settings, has_image=lambda _: False)
        state = self.request(settings, host, action="install", version="26.11.1")
        self.assertEqual(state["status"], "failed")
        self.assertIn("data as before", state["error"])
        self.assertEqual((data / "meta.json").read_text(), "before")
        self.assertEqual(state["installed"], "26.10.1-dev+4d28a33")
        # Going back once more (the kit is the previous one already) runs the previous installer only.
        state = self.request(settings, host, action="rollback", restore_data=True)
        self.assertEqual(state["status"], "current", state.get("error"))
        self.assertEqual(host.installers, ["26.11.1", "26.10.1-dev+4d28a33", "26.10.1-dev+4d28a33"])

    def test_an_interrupted_install_stays_visible(self):
        self.release("26.11.1")
        settings = self.settings()
        host = FakeHost({})
        up.check(settings, has_image=lambda _: False)
        state = up.read_state(settings)
        state.update({"status": "installing", "step": "install",
                      "last": {"version": "26.11.1", "previous": {"label": "26.10.1-dev+4d28a33", "images": {}}}})
        up.save_state(settings, state)
        state = up.check(settings, has_image=lambda _: False)
        self.assertEqual((state["status"], state["interrupted"]), ("failed", True))
        self.assertIn("interrupted while installing (install)", state["error"])
        self.assertEqual(up.check(settings, has_image=lambda _: False)["status"], "failed")
        state = self.request(settings, host, action="install", version="26.11.1")
        self.assertIn("roll back first", state["error"])
        self.assertEqual(host.installers, [])
        state = self.request(settings, host, action="rollback")
        self.assertEqual(state["status"], "current", state.get("error"))
        self.assertNotIn("interrupted", state)

    def test_request_fields_are_checked_before_they_are_kept(self):
        settings = self.settings()
        settings.share_dir.mkdir(parents=True)
        (settings.share_dir / "request.json").write_text(json.dumps(
            {"action": "x /tmp/p.so y", "version": "26.11.1 /etc", "by": "a b"}))
        state = up.handle_request(settings, FakeHost({}))
        self.assertEqual({k: state["request"][k] for k in ("action", "version", "by")},
                         {"action": None, "version": None, "by": None})
        self.assertFalse((settings.share_dir / "request.json").exists())

    @unittest.skipUnless(os.name == "posix", "symlink handling of the box")
    def test_links_in_the_shared_folder_are_not_followed(self):
        settings = self.settings()
        victim = self.base / "victim"
        victim.write_text("keep")
        settings.share_dir.mkdir(parents=True)
        os.symlink(victim, settings.share_dir / "state.json")
        os.symlink(victim, settings.share_dir / "request.json")
        up.handle_request(settings, FakeHost({}))
        self.assertEqual(victim.read_text(), "keep")
        self.assertFalse((settings.share_dir / "state.json").is_symlink())
        self.assertFalse(os.path.lexists(settings.share_dir / "request.json"))

    def test_a_first_installation_from_a_stick_checks_the_kit_and_fetches_the_images(self):
        self.release("26.11.1")
        stick_kit = self.base / "release-26.11.1" / "matrix-kit.tgz"
        settings = up.Settings({"MATRIX_UPDATE": "on", "MATRIX_UPDATE_URL": self.url,
                                "MATRIX_UPDATE_CHANNEL": "stable"}, state_dir=self.base / "fresh",
                               share_dir=self.base / "fresh-share", kit_dir=self.base / "fresh-kit",
                               allow_http=True, root=self.base / "data")
        host = FakeHost({})
        state = up.bootstrap(settings, stick_kit, host)
        self.assertEqual((state["status"], state["installed"]), ("current", "26.11.1"), state.get("error"))
        self.assertEqual(host.installers, ["26.11.1"])
        self.assertEqual(len([c for c in host.commands if c[:2] == ["docker", "load"]]), 2)
        # The kit on the stick must be the signed one.
        tampered = self.base / "tampered.tgz"
        tampered.write_bytes(stick_kit.read_bytes()[:-8] + b"\0" * 8)
        with self.assertRaises(up.UpdateError):
            up.bootstrap(settings, tampered, FakeHost({}))

    def test_bad_requests_change_nothing(self):
        self.release("26.11.1")
        settings = self.settings()
        host = FakeHost({})
        up.check(settings, has_image=lambda _: False)
        for request in ({"action": "install", "version": "26.12.1"}, {"action": "install", "version": "x; rm"},
                        {"action": "format-disk"}):
            state = self.request(settings, host, **request)
            self.assertIn("UpdateError", state["error"])
        kit = up.read_state(settings)["available"]["files"]["kit"]
        Path(kit).write_bytes(b"changed after the download")
        state = self.request(settings, host, action="install", version="26.11.1")
        self.assertIn("changed on disk", state["error"])
        self.assertEqual((host.commands, host.installers), ([], []))

    def test_a_newer_release_is_downloaded_and_offered_but_not_installed(self):
        self.release("26.11.1")
        state = up.check(self.settings(), has_image=lambda image_id: image_id == ADAPTER_ID)
        self.assertEqual((state["status"], state["available"]["version"]), ("ready", "26.11.1"), state)
        files = state["available"]["files"]
        self.assertEqual(list(files["images"]), ["vivesec-graph"])  # the box has the adapter image already
        self.assertTrue(Path(files["kit"]).read_bytes().startswith(b"\x1f\x8b"))  # the kit tgz
        shared = json.loads((self.base / "state-share" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(shared["available"]["notes"]["hu"], "Javítások")
        self.assertNotIn("files", shared["available"])
        self.assertTrue(any(" box=mx-" in agent and " v=26.10.1-dev+4d28a33 " in agent for agent in Handler.agents))

    def test_current_blocked_and_off(self):
        self.release("26.11.1", min_from="26.11.1")
        self.assertEqual(up.check(self.settings(), has_image=lambda _: False)["status"], "blocked")
        self.set_installed("26.11.1")
        self.assertEqual(up.check(self.settings("s2"), has_image=lambda _: False)["status"], "current")
        for values in ({"MATRIX_UPDATE": "yes"}, {"MATRIX_UPDATE_CHANNEL": "beta"}):
            self.assertEqual(up.check(self.settings("s3", **values))["status"], "off")
        plain = up.Settings({"MATRIX_UPDATE": "on", "MATRIX_UPDATE_URL": self.url}, state_dir=self.base / "s4",
                            share_dir=None, kit_dir=self.kit)
        self.assertFalse(plain.enabled)  # http:// is refused outside the tests

    def test_a_tampered_file_is_refused(self):
        out = self.release("26.11.1")
        stored = self.repo / "targets" / "kits" / ("%s.%s" % (out["kit"]["sha256"], out["kit"]["target"][5:]))
        stored.write_bytes(b"evil kit")
        state = up.check(self.settings(), has_image=lambda _: True)
        self.assertEqual(state["status"], "error")
        self.assertNotIn("files", state.get("available") or {})

    def test_expired_metadata_is_refused(self):
        self.release("26.11.1")
        days = dict(repo_tool.EXPIRY_DAYS)
        try:
            repo_tool.EXPIRY_DAYS["timestamp"] = -1
            repo_tool.refresh(self.repo, self.keys, PASSPHRASE)
        finally:
            repo_tool.EXPIRY_DAYS.update(days)
        state = up.check(self.settings(), has_image=lambda _: True)
        self.assertEqual(state["status"], "error")
        self.assertIn("Expired", state["error"])

    def test_publishing_rules(self):
        self.release("26.11.1")
        self.assertRaises(repo_tool.RepoError, self.release, "26.11.1")
        self.assertRaises(repo_tool.RepoError, self.release, "26.10.9")
        self.assertRaises(repo_tool.RepoError, self.release, "26.11.2.1")  # builds only on pilot/labor
        self.release("26.11.2.1", channel="labor")
        # An unchanged image is stored once, whichever release names it, even
        # though docker save wrote it again with other bytes.
        self.release("26.11.2", images=(("vivesec-adapter", ADAPTER_ID),))
        stored = list((self.repo / "targets" / "images").iterdir())
        self.assertEqual(len([p for p in stored if "vivesec-adapter" in p.name]), 1)
        self.assertIn("channel stable 26.11.2", repo_tool.status(self.repo))

    def test_keys_need_their_passphrase_and_development_keys_stay_on_labor(self):
        self.assertRaises(repo_tool.RepoError, repo_tool.refresh, self.repo, self.keys)
        self.assertRaises(repo_tool.RepoError, repo_tool.refresh, self.repo, self.keys, "x" * 20)
        self.assertIn("BEGIN ENCRYPTED PRIVATE KEY", (self.keys / "targets.pem").read_text())
        dev_repo, dev_keys = self.base / "dev-repo", self.base / "dev-keys"
        repo_tool.init(dev_repo, dev_keys)
        self.repo, self.keys = dev_repo, dev_keys
        self.assertRaises(repo_tool.RepoError, self.release, "26.11.1", channel="pilot")
        repo_tool.publish(dev_repo, dev_keys, "labor", self.base / "release-26.11.1")

    def test_a_quiet_repository_stays_valid_with_refresh(self):
        self.release("26.11.1")
        before = repo_tool.current(self.repo)[0].signed.version
        expires = repo_tool.refresh(self.repo, self.keys, PASSPHRASE)
        self.assertGreater(repo_tool.current(self.repo)[0].signed.version, before)
        self.assertGreater(expires, dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=13))
        self.assertEqual(up.check(self.settings(), has_image=lambda _: True)["status"], "ready")


class VersionTest(unittest.TestCase):
    def test_order(self):
        keys = [up.version_key(v) for v in ("26.10.1-dev+abc", "26.10.1", "26.10.1.3", "26.10.2", "26.11.1")]
        self.assertEqual(keys, sorted(keys))
        self.assertIsNone(up.version_key("latest"))


if __name__ == "__main__":
    unittest.main()
