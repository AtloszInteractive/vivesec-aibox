"""Tests for the base-image flattening of 172-release-box.py.

  Push-Location scripts/jetson; python -m unittest release_flatten_test; Pop-Location
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("release_box", os.path.join(HERE, "172-release-box.py"))
release_box = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_box)

CONFIG = {
    "Env": ["PATH=/usr/local/bin:/usr/bin", "GREETING=costs $5", "EMPTY="],
    "Labels": {"maintainer": "box", "vivesec.version": "26.10.1"},
    "ExposedPorts": {"8090/tcp": {}},
    "WorkingDir": "/app",
    "User": "1000:1000",
    "StopSignal": "SIGTERM",
    "Healthcheck": {"Test": ["CMD-SHELL", "curl -fs localhost:8090/health"], "Interval": 30_000_000_000,
                    "Timeout": 5_000_000_000, "Retries": 3},
    "Entrypoint": ["python", "-u"],
    "Cmd": ["rag_service/service.py"],
}


class FlattenDockerfileTest(unittest.TestCase):
    def test_copies_the_root_filesystem_into_one_layer_and_keeps_the_configuration(self):
        lines = release_box.flatten_dockerfile("vivesec-rag:release-1", CONFIG).splitlines()
        self.assertEqual(["FROM vivesec-rag:release-1 AS previous", "FROM scratch", "COPY --from=previous / /"],
                         lines[:3])
        self.assertEqual(1, sum(line.startswith(("RUN", "COPY", "ADD")) for line in lines))
        self.assertIn('ENV PATH="/usr/local/bin:/usr/bin"', lines)
        self.assertIn('ENV GREETING="costs \\$5"', lines)
        self.assertIn('ENV EMPTY=""', lines)
        self.assertIn('LABEL "maintainer"="box"', lines)
        self.assertIn("EXPOSE 8090/tcp", lines)
        self.assertIn("WORKDIR /app", lines)
        self.assertIn("USER 1000:1000", lines)
        self.assertIn("STOPSIGNAL SIGTERM", lines)
        self.assertIn("HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fs localhost:8090/health",
                      lines)
        self.assertEqual('ENTRYPOINT ["python", "-u"]', lines[-2])
        self.assertEqual('CMD ["rag_service/service.py"]', lines[-1])

    def test_an_exec_form_or_disabled_health_check_is_kept(self):
        exec_form = dict(CONFIG, Healthcheck={"Test": ["CMD", "curl", "-f", "x"]})
        self.assertIn('HEALTHCHECK CMD ["curl", "-f", "x"]',
                      release_box.flatten_dockerfile("i", exec_form).splitlines())
        disabled = dict(CONFIG, Healthcheck={"Test": ["NONE"]})
        self.assertIn("HEALTHCHECK NONE", release_box.flatten_dockerfile("i", disabled).splitlines())

    def test_a_minimal_configuration_gives_only_the_copy(self):
        self.assertEqual("FROM i AS previous\nFROM scratch\nCOPY --from=previous / /\n",
                         release_box.flatten_dockerfile("i", {"Env": None, "Cmd": None}))


class FlattenLimitTest(unittest.TestCase):
    def limit(self, value):
        with mock.patch.dict(os.environ, {"RELEASE_FLATTEN_LAYERS": value}):
            return release_box.flatten_limit()

    def test_default_off_invalid_and_capped(self):
        self.assertEqual(80, self.limit(""))
        self.assertEqual(80, self.limit("many"))
        self.assertEqual(0, self.limit("0"))
        self.assertEqual(0, self.limit("-5"))
        self.assertEqual(40, self.limit("40"))
        # Above the cap the build would fail on depth again.
        self.assertEqual(110, self.limit("500"))


class ReleaseBaseTest(unittest.TestCase):
    def run_base(self, layers, limit="80"):
        details = [{"RootFS": {"Layers": ["l"] * layers}, "Config": CONFIG}]
        calls = []

        def command(*args):
            calls.append(args)
            if args[:3] == ("docker", "image", "inspect"):
                return json.dumps(details)
            return ""

        with tempfile.TemporaryDirectory() as stage, \
                mock.patch.object(release_box, "command", side_effect=command), \
                mock.patch.dict(os.environ, {"RELEASE_FLATTEN_LAYERS": limit}), \
                mock.patch("builtins.print"):
            base = release_box.release_base("rag", "vivesec-rag:release-1", "20261025-150000", Path(stage))
            left = os.listdir(stage)
        return base, calls, left

    def test_a_shallow_image_is_used_as_it_is(self):
        base, calls, _ = self.run_base(79)
        self.assertEqual("vivesec-rag:release-1", base)
        self.assertEqual(1, len(calls))

    def test_a_deep_image_is_flattened_into_a_new_tag(self):
        base, calls, left = self.run_base(123)
        self.assertEqual("vivesec-rag:flat-20261025-150000", base)
        build = calls[1]
        self.assertEqual(("docker", "build", "-q", "-t", "vivesec-rag:flat-20261025-150000"), build[:5])
        self.assertTrue(build[5].endswith("flatten-rag"))
        self.assertEqual([], left)

    def test_flattening_can_be_turned_off(self):
        base, calls, _ = self.run_base(123, limit="0")
        self.assertEqual("vivesec-rag:release-1", base)
        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
