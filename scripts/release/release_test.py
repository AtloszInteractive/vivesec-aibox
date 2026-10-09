"""Tests for the E01 release tooling: build identity, configuration register,
release and box manifests, fleet overview.

  Push-Location scripts/release; python -m unittest discover -p "*_test.py"; Pop-Location
"""
import datetime
import json
import os
import shutil
import tempfile
import unittest

import box_manifest
import build_info
import config_registry
import fleet_status
import release_manifest

ROOT = build_info.ROOT
NOW = datetime.datetime(2026, 10, 9, 9, 0, tzinfo=datetime.timezone.utc)


class BuildInfoTest(unittest.TestCase):
    def test_label_marks_everything_but_a_clean_tagged_build_as_dev(self):
        sha = "0123456789abcdef"
        self.assertEqual(build_info.label("26.10.1", sha, False, True), "26.10.1")
        self.assertEqual(build_info.label("26.10.1", sha, True, True), "26.10.1-dev+0123456.dirty")
        self.assertEqual(build_info.label("26.10.1", sha, False, False), "26.10.1-dev+0123456")
        self.assertEqual(build_info.label("26.10.1", None, False, False), "26.10.1-dev")

    def test_repository_version_has_the_calendar_format(self):
        self.assertRegex(build_info.read_version(), build_info.VERSION_RE)

    def test_invalid_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            for bad in ("2026.10.1", "26.13.1", "26.1.1", "26.10.01", "v26.10.1"):
                with open(os.path.join(root, "VERSION"), "w", encoding="utf-8") as handle:
                    handle.write(bad)
                with self.assertRaises(ValueError, msg=bad):
                    build_info.read_version(root)

    def test_tree_without_git_keeps_the_shipped_stamp(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "adapter"))
            os.makedirs(os.path.join(root, "rag_service"))
            shipped = {"version": "26.10.1", "commit": "f" * 40, "label": "26.10.1"}
            with open(os.path.join(root, "adapter", "build_info.json"), "w", encoding="utf-8") as handle:
                json.dump(shipped, handle)
            unstamped = {"schema": 1, "version": "26.10.1", "commit": None, "dirty": None,
                         "tagged": False, "label": "26.10.1-dev", "built_at": "x"}
            written = build_info.stamp(root, info=unstamped)
            self.assertEqual(written, ["rag_service/build_info.json"])
            with open(os.path.join(root, "adapter", "build_info.json"), encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["commit"], "f" * 40)

    def test_stale_or_commitless_stamp_is_replaced(self):
        # A tree of a newer release copied over an old one without re-stamping
        # must not keep reporting the old release.
        unstamped = {"schema": 1, "version": "26.11.1", "commit": None, "dirty": None,
                     "tagged": False, "label": "26.11.1-dev", "built_at": "x"}
        for old in ({"version": "26.10.1", "commit": "f" * 40, "label": "26.10.1"},
                    {"version": "26.11.1", "commit": None, "label": "26.11.1-dev"}):
            with tempfile.TemporaryDirectory() as root:
                os.makedirs(os.path.join(root, "adapter"))
                path = os.path.join(root, "adapter", "build_info.json")
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(old, handle)
                written = build_info.stamp(root, info=unstamped, targets=("adapter/build_info.json",))
                self.assertEqual(written, ["adapter/build_info.json"], old)
                with open(path, encoding="utf-8") as handle:
                    self.assertEqual(json.load(handle)["label"], "26.11.1-dev")

    def test_release_check_lists_every_problem(self):
        info = {"version": "26.10.1", "commit": None, "dirty": True, "tagged": False}
        self.assertEqual(len(build_info.release_problems(info)), 3)
        info = {"version": "26.10.1", "commit": "a" * 40, "dirty": False, "tagged": True}
        self.assertEqual(build_info.release_problems(info), [])

    def test_component_loaders_are_identical(self):
        with open(os.path.join(ROOT, "adapter", "build_info.py"), "rb") as adapter, \
                open(os.path.join(ROOT, "rag_service", "build_info.py"), "rb") as rag:
            self.assertEqual(adapter.read().replace(b"\r\n", b"\n"), rag.read().replace(b"\r\n", b"\n"))


class ConfigRegistryTest(unittest.TestCase):
    def test_register_covers_the_code_and_the_images(self):
        self.assertEqual(config_registry.check(), [])

    def test_rendered_document_is_current(self):
        with open(config_registry.DOC_PATH, encoding="utf-8") as handle:
            current = handle.read().replace("\r\n", "\n")
        self.assertEqual(current, config_registry.render(),
                         "run: python scripts/release/config_registry.py render")

    def test_schema_problems_are_reported(self):
        registry = {"parameters": [
            {"name": "X_A", "component": "adapter", "category": "network", "type": "enum",
             "default": None, "allowed": "a", "fail_safe": "f", "impact": "i", "change": "restart",
             "secret": False, "internal": False},
            {"name": "X_A", "component": "nowhere"},
            {"name": "X_KEY", "component": "adapter", "category": "security", "type": "secret",
             "default": "hunter2", "allowed": "a", "fail_safe": "f", "impact": "i", "change": "restart",
             "secret": True, "internal": False},
        ]}
        problems = " ".join(config_registry.schema_problems(registry))
        self.assertIn("enum without values", problems)
        self.assertIn("missing", problems)
        self.assertIn("must not carry a default", problems)

    def test_runtime_validation(self):
        problems = config_registry.validate_env({
            "ADAPTER_PORT": "eighty",
            "ADAPTER_CHAT_POLICY": "open_everything",
            "ADAPTER_CHAT_POLCY": "locked_grounded",
            "ADAPTER_SCOPE_ALL_DRIVES": "1",
            "RAG_API_KEY": "super-secret-value",
            "PATH": "/usr/bin",
        })
        text = "\n".join(problems)
        self.assertIn("ADAPTER_PORT='eighty': not a valid int", text)
        self.assertIn("ADAPTER_CHAT_POLICY", text)
        self.assertIn("ADAPTER_CHAT_POLCY: not a known parameter", text)
        self.assertIn("ADAPTER_SCOPE_ALL_DRIVES: dev/test-only", text)
        self.assertNotIn("super-secret-value", text)
        self.assertNotIn("PATH", text)
        self.assertEqual(config_registry.validate_env({"ADAPTER_PORT": "8088",
                                                       "ADAPTER_CHAT_POLICY": "locked_hybrid"}), [])


class ReleaseManifestTest(unittest.TestCase):
    def test_manifest_records_the_release_without_secrets(self):
        if shutil.which("git") is None:
            self.skipTest("git not available")
        manifest = release_manifest.build(artifacts=[os.path.join(ROOT, "VERSION")], now=NOW)
        self.assertEqual(manifest["release"]["version"], build_info.read_version())
        self.assertEqual(set(manifest["code"]), {"adapter", "rag", "ui", "install_kit"})
        self.assertTrue(all(tree["files"] > 0 for tree in manifest["code"].values()))
        self.assertEqual(manifest["artifacts"][0]["name"], "VERSION")
        self.assertEqual(manifest["images"]["adapter"]["base"], "python:3.11-slim-bookworm")
        self.assertEqual(manifest["models"]["embedding"], "bge-m3")
        self.assertIn("ADAPTER_CHAT_POLICY", manifest["policies"])
        flat = json.dumps(manifest)
        for secret in ("RAG_API_KEY", "TS_AUTH_KEY"):
            self.assertNotIn(secret, flat)


def _inspect(env, labels=None):
    return json.dumps([{"Image": "sha256:" + "1" * 64,
                        "Config": {"Image": "vivesec-x:latest", "Env": env, "Labels": labels or {}},
                        "State": {"Running": True, "StartedAt": "2026-10-09T08:00:00Z"}}])


class BoxManifestTest(unittest.TestCase):
    def setUp(self):
        self.build = {"label": "26.10.1", "commit": "a" * 40, "version": "26.10.1"}
        self.urls = []
        self.ui_label = "26.10.1"

    def run_cmd(self, *args):
        if args[:2] == ("docker", "inspect"):
            return {
                "vivesec-adapter": _inspect(["ADAPTER_PORT=8088", "RAG_API_KEY=top-secret",
                                             "ADAPTER_GEN_MODEL=qwen3.6:35b", "ADAPTER_CHAT_POLICY=locked_hybrid"]),
                "vivesec-rag": _inspect(["RAG_PORT=8090", "RAG_API_KEY=top-secret", "VENDOR_TOKEN=x"]),
                "vivesec-ui": _inspect(["PORT=8080", "ADAPTER_URL=http://127.0.0.1:8088"],
                                       {"vivesec.version": "26.10.1"}),
            }[args[2]]
        return "5.15.148-tegra\n" if args[0] == "uname" else "27.3.1\n"

    def http(self, url, timeout=10):
        self.urls.append(url)
        if url.endswith("/api/v1/version"):
            return {"ok": True, "paired": True, "ws_fs": {"connected": True}, "storage_locked": False,
                    "version": {"release": "26.10.1", "consistent": True,
                                "components": {"adapter": self.build, "rag": self.build}}}
        if url.endswith("/version.json"):
            return {"label": self.ui_label, "version": self.ui_label.split("-")[0]}
        if url.endswith("/api/version"):
            return {"version": "0.34.0"}
        if url.endswith("/api/tags"):
            return {"models": [{"name": "qwen3.6:35b", "digest": "d1", "size": 1},
                               {"name": "bge-m3:latest", "digest": "d2", "size": 2}]}
        raise AssertionError(url)

    def collect(self):
        manifest = box_manifest.collect(run=self.run_cmd, http_json=self.http, now=NOW)
        manifest["platform"]["l4t"] = box_manifest.parse_l4t(
            "# R36 (release), REVISION: 4.4, GCID: 41062509, BOARD: generic, EABI: aarch64")
        manifest["checks"] = box_manifest.compatibility_checks(manifest, manifest["compatibility"])
        return manifest

    def test_l4t_release_line_is_parsed(self):
        self.assertEqual(box_manifest.parse_l4t("# R36 (release), REVISION: 4.4, GCID: 1"), "R36.4.4")
        self.assertIsNone(box_manifest.parse_l4t(None))

    def test_healthy_box(self):
        manifest = self.collect()
        failed = [c["check"] for c in manifest["checks"] if not c["ok"]]
        self.assertEqual(failed, [])
        self.assertEqual(manifest["pairing"]["paired"], True)
        self.assertEqual(manifest["containers"]["adapter"]["version"], "26.10.1")
        self.assertEqual(manifest["errors"], [])

    def test_secrets_are_never_written(self):
        manifest = self.collect()
        flat = json.dumps(manifest)
        self.assertNotIn("top-secret", flat)
        self.assertEqual(manifest["containers"]["adapter"]["env"]["RAG_API_KEY"], "<set>")
        self.assertEqual(manifest["containers"]["rag"]["env"]["VENDOR_TOKEN"], "<set>")

    def test_never_polls_status(self):
        self.collect()
        self.assertFalse([url for url in self.urls if "/api/v1/status" in url])

    def test_ui_from_another_release_fails_the_check(self):
        self.ui_label = "26.11.1"
        failed = [c["check"] for c in self.collect()["checks"] if not c["ok"]]
        self.assertEqual(failed, ["components carry the same version"])


class FleetStatusTest(unittest.TestCase):
    @staticmethod
    def payload(release, consistent=True, paired=True):
        build = {"label": release}
        return {"ok": True, "paired": paired, "ws_fs": {"connected": paired}, "storage_locked": False,
                "version": {"release": release, "consistent": consistent,
                            "components": {"adapter": build, "rag": build if consistent else None}}}

    def test_drift_unreachable_and_mixed_builds_are_reported(self):
        answers = {
            "a": (self.payload("26.10.1"), {"label": "26.10.1"}),
            "b": (self.payload("26.11.1", consistent=False, paired=False), None),
        }

        def fetcher(box):
            if box["name"] == "c":
                raise OSError("connection refused")
            return answers[box["name"]]

        report = fleet_status.collect([{"name": "a"}, {"name": "b"}, {"name": "c"}], fetcher)
        self.assertEqual(report["releases"], ["26.10.1", "26.11.1"])
        self.assertTrue(report["drift"])
        self.assertEqual(report["unreachable"], ["c"])
        self.assertEqual(report["inconsistent"], ["b"])
        text = fleet_status.render(report)
        self.assertIn("UNREACHABLE: connection refused", text)
        self.assertIn("more than one release", text)
        self.assertEqual(report["boxes"][0]["ui"], "26.10.1")
        self.assertIs(report["boxes"][1]["paired"], False)

    def test_ssh_box_is_queried_on_its_loopback_version_endpoint(self):
        calls = []

        def ssh_json(target, url):
            calls.append((target, url))
            return self.payload("26.10.1") if url.endswith("/api/v1/version") else {"label": "26.10.1"}

        payload, ui = fleet_status.fetch({"ssh": "aibox@box"}, ssh_json=ssh_json)
        self.assertEqual(calls, [("aibox@box", "http://127.0.0.1:80/api/v1/version"),
                                 ("aibox@box", "http://127.0.0.1:8080/version.json")])
        self.assertEqual(ui["label"], "26.10.1")


if __name__ == "__main__":
    unittest.main()
