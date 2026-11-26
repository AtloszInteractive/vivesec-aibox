import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import spark_install as si


class FilesTest(unittest.TestCase):
    """Some folders are written by containers too: the installer must not write through their links."""

    @unittest.skipUnless(os.name == "posix", "symlink handling of the box")
    def test_a_link_is_replaced_not_written_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            victim = Path(tmp, "victim")
            victim.write_text("keep")
            for name in ("acl-rules.json", "tunnel.key"):
                os.symlink(victim, Path(tmp, name))
            si.write_file(Path(tmp, "acl-rules.json"), "{}\n", 0o600)
            si.ensure_key(Path(tmp, "tunnel.key"))
            self.assertEqual(victim.read_text(), "keep")
            self.assertFalse(Path(tmp, "tunnel.key").is_symlink())
            self.assertEqual(len(Path(tmp, "tunnel.key").read_text().strip()), 64)

    def test_a_short_key_is_replaced_and_a_good_one_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp, "k")
            key.write_text("short")
            si.ensure_key(key)
            first = key.read_text()
            self.assertEqual(len(first.strip()), 64)
            si.ensure_key(key)
            self.assertEqual(key.read_text(), first)


class SettingsTest(unittest.TestCase):
    def test_defaults_are_safe(self):
        conf, warnings = si.settings("", env={})
        self.assertEqual((conf["MATRIX_TUNNEL_PKI"], conf["MATRIX_TUNNEL_FIREWALL"]), ("off", "off"))
        self.assertEqual((conf["MATRIX_ADAPTER_PORT"], conf["MATRIX_TUNNEL_PORT"]), (8088, 7476))
        self.assertFalse(conf["MATRIX_ACCEPT_EMBED_CHANGE"])
        self.assertEqual(warnings, [])

    def test_file_then_environment(self):
        text = "# comment\nMATRIX_TUNNEL_PKI=files\nMATRIX_GEN_MODEL='qwen3.6:35b'  # inline\n"
        conf, _ = si.settings(text, env={"MATRIX_TUNNEL_PKI": "partner-dev", "OTHER": "x"})
        self.assertEqual((conf["MATRIX_TUNNEL_PKI"], conf["MATRIX_GEN_MODEL"]), ("partner-dev", "qwen3.6:35b"))

    def test_unknown_modes_fall_back_to_off(self):
        conf, warnings = si.settings("MATRIX_TUNNEL_PKI=partner\nMATRIX_TUNNEL_FIREWALL=open\n", env={})
        self.assertEqual((conf["MATRIX_TUNNEL_PKI"], conf["MATRIX_TUNNEL_FIREWALL"]), ("off", "off"))
        self.assertEqual(len(warnings), 2)

    def test_the_other_products_ports_and_bad_values_are_refused(self):
        for text in ("MATRIX_TUNNEL_PORT=443", "MATRIX_UI_PORT=8443", "MATRIX_RAG_PORT=11434",
                     "MATRIX_ADAPTER_PORT=80", "MATRIX_UI_PORT=8088", "MATRIX_UI_PORT=x",
                     "MATRIX_OLLAMA_URL=http://10.0.0.5:11434", "MATRIX_NUM_CTX=big", "not a line"):
            self.assertRaises(si.InstallError, si.settings, text, {})

    def test_updates_are_off_unless_an_https_channel_is_given(self):
        conf, _ = si.settings("", env={})
        self.assertEqual((conf["MATRIX_UPDATE"], conf["MATRIX_UPDATE_CHANNEL"]), ("off", "stable"))
        for text in ("MATRIX_UPDATE=on", "MATRIX_UPDATE=on\nMATRIX_UPDATE_URL=http://updates.example/matrix"):
            self.assertRaises(si.InstallError, si.settings, text, {})
        conf, warnings = si.settings("MATRIX_UPDATE=on\nMATRIX_UPDATE_CHANNEL=beta\n", env={})
        self.assertEqual((conf["MATRIX_UPDATE"], len(warnings)), ("off", 1))
        conf, _ = si.settings("MATRIX_UPDATE=on\nMATRIX_UPDATE_URL=https://updates.example/matrix/\n", env={})
        self.assertEqual(conf["MATRIX_UPDATE_URL"], "https://updates.example/matrix")
        units = si.updater_units()
        self.assertIn("PathExists=/var/lib/vivesec-matrix/adapter/updates/request.json",
                      units["vivesec-matrix-updater-request.path"])
        self.assertIn("Environment=PYTHONPATH=/opt/vivesec-matrix/updater/lib",
                      units["vivesec-matrix-updater-check.service"])
        self.assertIn("/usr/bin/python3 -S ", units["vivesec-matrix-updater-request.service"])

    def test_vault_is_off_unless_asked_and_unclear_values_stop(self):
        conf, _ = si.settings("", env={})
        self.assertEqual((conf["MATRIX_VAULT"], conf["MATRIX_VAULT_UNLOCK"]), ("off", "keyfile"))
        conf, _ = si.settings("MATRIX_VAULT=ON\nMATRIX_VAULT_SIZE=2t\n", env={})
        self.assertEqual((conf["MATRIX_VAULT"], conf["MATRIX_VAULT_SIZE"]), ("on", "2T"))
        for text in ("MATRIX_VAULT=yes", "MATRIX_VAULT=on\nMATRIX_VAULT_UNLOCK=passphrase",
                     "MATRIX_VAULT=on\nMATRIX_VAULT_SIZE=big", "MATRIX_VAULT=on\nMATRIX_VAULT_SIZE=0G",
                     "MATRIX_VAULT=on\nMATRIX_VAULT_RECOVERY_OUT=relative.txt",
                     "MATRIX_VAULT=on\nMATRIX_VAULT_RECOVERY_OUT=/var/lib/vivesec-matrix/key.txt",
                     "MATRIX_VAULT=on\nMATRIX_VAULT_RECOVERY_OUT=/root/../etc/key"):
            self.assertRaises(si.InstallError, si.settings, text, {})

    def test_vault_conf_replaces_only_its_own_lines(self):
        conf, _ = si.settings("MATRIX_VAULT=on\nMATRIX_VAULT_SIZE=1T\n", env={})
        values = si.vault_settings(conf)
        text = si.vault_conf_text("AIBOX_X=1\nAIBOX_VAULT_SIZE=old\n", values)
        self.assertIn("AIBOX_X=1\n", text)
        self.assertEqual(text.count("AIBOX_VAULT_SIZE="), 1)
        self.assertIn("AIBOX_VAULT_SIZE=1T\n", text)
        self.assertIn("AIBOX_VAULT_LINKS=/var/lib/vivesec-matrix\n", text)
        self.assertIn("Requires=aibox-vault.service", si.vault_dropin_text())

    def test_graph_is_off_unless_asked_and_unknown_values_fall_back(self):
        self.assertEqual(si.settings("", env={})[0]["MATRIX_GRAPH"], "off")
        conf, warnings = si.settings("MATRIX_GRAPH=yes\n", env={})
        self.assertEqual((conf["MATRIX_GRAPH"], len(warnings)), ("off", 1))
        for text in ("MATRIX_GRAPH_PORT=11434", "MATRIX_NEO4J_PORT=8088", "MATRIX_GRAPH_ADMINS=' , '",
                     "MATRIX_GRAPH_PROFILE='../x'"):
            self.assertRaises(si.InstallError, si.settings, text, {})

    def test_own_ollama_needs_a_loopback_port_of_its_own_and_unclear_modes_stop(self):
        conf, _ = si.settings("", env={})
        self.assertEqual((conf["MATRIX_OLLAMA"], conf["MATRIX_OLLAMA_PORT"]), ("shared", None))
        conf, _ = si.settings("MATRIX_OLLAMA=Own\nMATRIX_OLLAMA_URL=http://127.0.0.1:11435/\n", env={})
        self.assertEqual((conf["MATRIX_OLLAMA"], conf["MATRIX_OLLAMA_PORT"]), ("own", 11435))
        for text in ("MATRIX_OLLAMA=yes", "MATRIX_OLLAMA=own",
                     "MATRIX_OLLAMA=own\nMATRIX_OLLAMA_URL=http://localhost:11435",
                     "MATRIX_OLLAMA=own\nMATRIX_OLLAMA_URL=http://127.0.0.1:8088",
                     "MATRIX_OLLAMA_IMAGE='ollama/ollama;rm'"):
            self.assertRaises(si.InstallError, si.settings, text, {})

    def test_own_ollama_runs_on_the_gpu_on_loopback_with_the_adapters_context(self):
        conf, _ = si.settings("MATRIX_OLLAMA=own\nMATRIX_OLLAMA_URL=http://127.0.0.1:11435\n", env={})
        args = si.ollama_args(conf)
        self.assertIn("nvidia.com/gpu=all", args)
        self.assertIn("OLLAMA_HOST=127.0.0.1:11435", args)
        self.assertIn("OLLAMA_CONTEXT_LENGTH=65536", args)
        self.assertIn("/var/lib/vivesec-matrix-ollama:/models", args)
        self.assertIn("/etc/vivesec-matrix/ollama-gai.conf:/etc/gai.conf:ro", args)
        self.assertIn("GODEBUG=netdns=cgo", args)
        self.assertIn("OLLAMA_URL=http://127.0.0.1:11435", si.adapter_args(conf, "k"))

    def test_a_changed_embedding_model_is_refused_unless_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = Path(tmp) / "embed-model.json"
            conf, _ = si.settings("", env={})
            models = {"bge-m3:latest": "b" * 64}
            self.assertIsNone(si.embed_change_problem(models, conf, record))
            record.write_text(json.dumps({"model": "bge-m3:latest", "digest": "a" * 64}))
            self.assertIn("changed", si.embed_change_problem(models, conf, record))
            self.assertIsNone(si.embed_change_problem({"bge-m3:latest": "a" * 64}, conf, record))
            conf, _ = si.settings("MATRIX_ACCEPT_EMBED_CHANGE=yes", env={})
            self.assertIsNone(si.embed_change_problem(models, conf, record))


class VaultPhaseTest(unittest.TestCase):
    """The vault phase must not order a timer or path unit after aibox-vault (an ordering
    cycle: systemd then drops paths.target at boot and docker's stop at shutdown), and
    must not stop the unit that runs it (the updater)."""

    UPDATER = "vivesec-matrix-updater-request.service"

    def run_phase(self, settled, me=UPDATER):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        units = tmp / "units"
        for unit in si.VAULT_DEPENDENTS + si.VAULT_TRIGGERS:
            (units / unit).parent.mkdir(parents=True, exist_ok=True)
            (units / unit).write_text("[Unit]\n")
        old = units / "vivesec-matrix-updater-request.path.d"
        old.mkdir()
        (old / si.VAULT_DROPIN).write_text("[Unit]\nAfter=aibox-vault.service\n")
        kept = units / "vivesec-matrix-pki-sync.timer.d"
        kept.mkdir()
        (kept / si.VAULT_DROPIN).write_text("[Unit]\nAfter=aibox-vault.service\n")
        (kept / "local.conf").write_text("[Timer]\n")
        (tmp / "vault.img").write_text("")
        root = mock.MagicMock()
        root.is_symlink.return_value = settled
        root.name = "vivesec-matrix"
        root.parent.glob.return_value = []
        calls = []
        conf, _ = si.settings("MATRIX_VAULT=on\n", env={})
        with mock.patch.multiple(si, UNIT_DIR=units, ROOT=root, VAULT_IMAGE=tmp / "vault.img",
                                 VAULT_MOUNT=tmp / "mnt", VAULT_CONF=tmp / "standalone.conf"), \
                mock.patch.object(si.os.path, "ismount", return_value=settled), \
                mock.patch.object(si, "unit_active", return_value=True), \
                mock.patch.object(si, "own_unit", return_value=me), \
                mock.patch.object(si, "vault_tool", side_effect=lambda *a: calls.append(("vault",) + a)), \
                mock.patch.object(si, "run", side_effect=lambda *a, **k: calls.append(a)):
            si.phase_vault(conf)
        dropins = sorted(p.parent.name[:-2] for p in units.glob("*.d/" + si.VAULT_DROPIN))
        return calls, dropins, units

    def test_only_services_get_the_drop_in_and_old_ones_go(self):
        _, dropins, units = self.run_phase(settled=True)
        self.assertEqual(dropins, sorted(si.VAULT_DEPENDENTS))
        self.assertFalse((units / "vivesec-matrix-updater-request.path.d").exists())
        self.assertTrue((units / "vivesec-matrix-pki-sync.timer.d" / "local.conf").is_file())

    def test_an_installed_volume_stops_nothing(self):
        calls, _, _ = self.run_phase(settled=True)
        self.assertNotIn("stop", [c[1] for c in calls if c[0] == "systemctl"])
        self.assertIn(("vault", "migrate"), calls)

    def test_moving_the_data_stops_its_users_but_never_the_running_updater(self):
        calls, _, _ = self.run_phase(settled=False)
        stopped = [c[2] for c in calls if c[:2] == ("systemctl", "stop")]
        self.assertIn("vivesec-matrix-tunnel.service", stopped)
        self.assertIn("vivesec-matrix-updater-request.path", stopped)
        self.assertNotIn(self.UPDATER, stopped)
        self.assertEqual(sorted(stopped), sorted(c[2] for c in calls if c[:2] == ("systemctl", "start")))

    def test_own_unit_reads_the_cgroup(self):
        for text, unit in (("0::/system.slice/vivesec-matrix-updater-request.service\n", self.UPDATER),
                           ("0::/user.slice/user-1000.slice/session-3.scope\n", "")):
            with mock.patch.object(si.Path, "read_text", return_value=text):
                self.assertEqual(si.own_unit(), unit)


class ContainerArgsTest(unittest.TestCase):
    def setUp(self):
        self.conf, _ = si.settings("", env={})

    def env_of(self, args):
        return dict(args[i + 1].split("=", 1) for i, arg in enumerate(args) if arg == "-e")

    def test_a_release_kit_brings_its_images_and_a_source_kit_builds(self):
        self.assertEqual(si.kit_images({"label": "26.10.1-dev+abc"}), {})
        image_id = "sha256:" + "c" * 64
        self.assertEqual(si.kit_images({"images": [{"name": "vivesec-graph", "id": image_id}]}),
                         {"vivesec-graph": image_id})
        for bad in ({"name": "graph", "id": image_id}, {"name": "vivesec-graph", "id": "latest"}):
            self.assertRaises(si.InstallError, si.kit_images, {"images": [bad]})

    def test_adapter_behaviour_comes_from_matrix_conf_but_not_its_wiring(self):
        text = "ADAPTER_APP_MODE=analyst\nADAPTER_CASE_REF_PATTERN='(?:19|20)\\d{2}/\\d{1,7}'\n"
        conf, _ = si.settings(text, env={"ADAPTER_APP_MODE": "full"})
        env = self.env_of(si.adapter_args(conf, "k" * 64))
        self.assertEqual(env["ADAPTER_APP_MODE"], "analyst")
        self.assertEqual(env["ADAPTER_CASE_REF_PATTERN"], "(?:19|20)\\d{2}/\\d{1,7}")
        self.assertNotIn("GRAPH_URL", env)
        for owned in ("ADAPTER_HOST=0.0.0.0", "ADAPTER_IDENTITY_MODE=off", "ADAPTER_TLS=on",
                      "ADAPTER_SESSION_DIR=/tmp", "ADAPTER_INTEGRATION=vivesec", "ADAPTER_GRAPH_ADMINS=x"):
            self.assertRaises(si.InstallError, si.settings, owned, {})

    def test_adapter_is_standalone_on_loopback_and_keeps_off_the_pairing_surfaces(self):
        env = self.env_of(si.adapter_args(self.conf, "k" * 64))
        self.assertEqual((env["ADAPTER_HOST"], env["ADAPTER_PORT"]), ("127.0.0.1", "8088"))
        self.assertEqual((env["ADAPTER_TLS"], env["ADAPTER_DISCOVERY"]), ("off", "off"))
        self.assertEqual((env["ADAPTER_INTEGRATION"], env["ADAPTER_IDENTITY_MODE"]), ("standalone", "require"))
        self.assertEqual(env["ADAPTER_NUM_CTX"], "65536")
        self.assertEqual(env["OLLAMA_URL"], "http://127.0.0.1:11434")
        self.assertTrue(all(value.startswith("/data") for key, value in env.items()
                            if key.endswith(("_DIR", "_PATH")) and key != "ADAPTER_DRIVE_PREFIX"))

    def test_ui_listens_on_loopback_only(self):
        env = self.env_of(si.ui_args(self.conf))
        self.assertEqual((env["HOST"], env["NITRO_HOST"], env["ADAPTER_INTEGRATION"]),
                         ("127.0.0.1", "127.0.0.1", "standalone"))
        self.assertNotIn("ADAPTER_DEMO_USER", env)


class TunnelConfigTest(unittest.TestCase):
    def test_front_requires_the_client_certificate_and_sets_the_trust_headers(self):
        conf, _ = si.settings("", env={})
        text = si.front_conf(conf, "f" * 64)
        self.assertIn("listen 7476 ssl;", text)
        self.assertIn("ssl_verify_client on;", text)
        self.assertIn('proxy_set_header X-Matrix-Forward-Key "%s";' % ("f" * 64), text)
        self.assertIn("proxy_set_header X-Matrix-Client-Verify $ssl_client_verify;", text)
        self.assertIn("location ^~ /api/v1/matrix/", text)
        self.assertIn("location / { return 404; }", text)

    def test_gateway_unit_takes_its_keys_as_credentials(self):
        conf, _ = si.settings("", env={})
        unit = si.gateway_unit(conf)
        self.assertIn("DynamicUser=yes", unit)
        self.assertEqual(unit.count("LoadCredential="), 3)
        self.assertIn("MATRIX_TUNNEL_FORWARD_KEY_FILE=%d/forward.key", unit)


class Neo4jKeptTest(unittest.TestCase):
    PORT, DATA = 7687, Path("/var/lib/vivesec-matrix/neo4j/data")

    def test_the_installers_own_container_passes(self):
        info = {"HostConfig": {"PortBindings": {"7687/tcp": [{"HostIp": "127.0.0.1", "HostPort": "7687"}]}},
                "Mounts": [{"Source": str(self.DATA), "Destination": "/data"}]}
        self.assertEqual(si.neo4j_problems(info, self.PORT, self.DATA), [])

    def test_a_placeholder_without_port_and_data_is_refused(self):
        # `docker create --name vivesec-neo4j <image>`: no published port, no volume.
        problems = si.neo4j_problems({"HostConfig": {"PortBindings": {}}, "Mounts": []}, self.PORT, self.DATA)
        self.assertEqual(len(problems), 2)
        public = {"HostConfig": {"PortBindings": {"7687/tcp": [{"HostIp": "", "HostPort": "7687"}]}},
                  "Mounts": [{"Source": str(self.DATA), "Destination": "/data"}]}
        self.assertEqual(len(si.neo4j_problems(public, self.PORT, self.DATA)), 1)


class InstallProbeTest(unittest.TestCase):
    def forget(self, shown):
        calls = []

        def run(*args, **_kw):
            calls.append(args[len(si.AUTH_MANAGE):])
            return subprocess.CompletedProcess(args, 0, stdout=shown, stderr="")

        with mock.patch.object(si, "run", run), mock.patch.object(si, "log"):
            si.forget_old_probe()
        return calls[1:]

    def test_the_old_probe_and_only_its_own_user_are_removed(self):
        auto = "vvs-" + hashlib.sha256(si.OLD_PROBE.encode()).hexdigest()[:10]
        shown = json.dumps({"users": {si.OLD_PROBE: auto, "28744CZP27222": "anna"}})
        self.assertEqual(self.forget(shown), [("tunnel-map", "remove", si.OLD_PROBE), ("delete-user", auto)])
        shown = json.dumps({"users": {si.OLD_PROBE: "anna"}})
        self.assertEqual(self.forget(shown), [("tunnel-map", "remove", si.OLD_PROBE)])
        self.assertEqual(self.forget(json.dumps({"users": {"x": "anna"}})), [])
        self.assertEqual(self.forget(""), [])
        self.assertEqual(self.forget("not json"), [])
        # The new probe is refused by its form, so it can never get a user.
        self.assertNotRegex(si.INSTALL_PROBE, r"^[A-Za-z0-9][A-Za-z0-9._@=+-]{0,127}$")


class ReleaseModelsTest(unittest.TestCase):
    def ollama(self, root, name, layers):
        """An Ollama models folder holding one model; returns its blobs by name."""
        root = Path(root)
        (root / "blobs").mkdir(parents=True, exist_ok=True)
        entries = []
        for content in layers:
            digest = hashlib.sha256(content).hexdigest()
            (root / "blobs" / ("sha256-" + digest)).write_bytes(content)
            entries.append({"digest": "sha256:" + digest, "size": len(content)})
        manifest = root / "manifests" / si.model_manifest(name)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"config": entries[0], "layers": entries[1:]}))

    def test_model_names_map_to_ollama_manifests(self):
        self.assertEqual(si.model_manifest("qwen3.6:35b"), "registry.ollama.ai/library/qwen3.6/35b")
        self.assertEqual(si.model_manifest("bge-m3"), "registry.ollama.ai/library/bge-m3/latest")
        self.assertEqual(si.model_manifest("acme/m:1"), "registry.ollama.ai/acme/m/1")
        for bad in ("../x", "a/../../b:1", "x:", "a/b/c/d", ""):
            self.assertRaises(si.InstallError, si.model_manifest, bad)

    def test_a_pinned_model_is_placed_only_from_files_matching_the_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            build, stick, box = Path(tmp) / "build", Path(tmp) / "stick", Path(tmp) / "box"
            self.ollama(build, "bge-m3", [b"config", b"weights" * 100])
            pin = si.model_pin(build, "bge-m3")
            self.assertEqual(si.kit_models({"models": [pin]}), [pin])
            self.assertEqual(si.place_model(pin, build, stick), 6 + 700)
            self.assertTrue(si.model_in_place(stick, pin))
            self.assertEqual(si.place_model(pin, stick, stick), 0)
            blob = next((stick / "blobs").iterdir())
            blob.write_bytes(b"X" * blob.stat().st_size)  # same size, other content
            self.assertRaises(si.InstallError, si.place_model, pin, stick, box, True)
            self.assertFalse((box / "manifests").exists())  # Ollama never sees a partial model
            si.place_model(pin, build, stick)  # a second run repairs the stick
            si.place_model(pin, stick, box, move=True)
            self.assertTrue(si.model_in_place(box, pin))
            self.ollama(stick, "bge-m3", [b"other config", b"other"])
            self.assertRaises(si.InstallError, si.place_model, pin, stick, Path(tmp) / "box2")

    def test_kit_models_refuse_bad_pins(self):
        pin = {"name": "bge-m3", "manifest": si.model_manifest("bge-m3"), "sha256": "a" * 64, "bytes": 1}
        self.assertEqual(si.kit_models({}), [])
        for bad in ({"manifest": "registry.ollama.ai/library/x/latest"}, {"sha256": "A" * 64},
                    {"name": "../x"}, {"bytes": "1"}):
            self.assertRaises(si.InstallError, si.kit_models, {"models": [dict(pin, **bad)]})


if __name__ == "__main__":
    unittest.main()
