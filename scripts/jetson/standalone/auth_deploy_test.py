"""Tests of auth_deploy.py: the configuration changes of the standalone switch
and the image recipe. The docker operations themselves run on the box."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth_deploy  # noqa: E402

ADAPTER_ENV = {"ADAPTER_PORT": "8088", "ADAPTER_HOST": "127.0.0.1", "ADAPTER_SCOPE_ALL_DRIVES": "1",
               "ADAPTER_IDENTITY_MODE": "off", "PATH": "/usr/bin"}
ADAPTER_BINDS = ["/data/adapter:/data", "/data/jobs:/data/jobs"]
UI_ENV = {"PORT": "8080", "HOST": "127.0.0.1", "ADAPTER_URL": "http://127.0.0.1:8088",
          "ADAPTER_DEMO_USER": "demo"}
UI_BINDS = ["/home/aibox/stage/build/.output:/app/.output:ro"]


class SwitchTest(unittest.TestCase):
    def test_enable_sets_the_standalone_preconditions(self):
        env, binds = auth_deploy.adapter_change(ADAPTER_ENV, ADAPTER_BINDS, "/data/auth",
                                                {"AUTH_JWT_ISSUER": "box-7"}, True)
        self.assertEqual(env["ADAPTER_INTEGRATION"], "standalone")
        self.assertEqual(env["ADAPTER_IDENTITY_MODE"], "require")
        self.assertEqual(env["ADAPTER_DIRECTORY"], "token")
        self.assertEqual(env["ADAPTER_IDENTITY_ALGORITHMS"], "HS256")
        self.assertEqual(env["ADAPTER_IDENTITY_HS256_SECRET_FILE"], auth_deploy.ADAPTER_KEY)
        self.assertEqual(env["ADAPTER_IDENTITY_ISSUER"], "box-7")
        self.assertEqual(env["ADAPTER_IDENTITY_AUDIENCE"], auth_deploy.DEFAULT_AUDIENCE)
        self.assertNotIn("ADAPTER_SCOPE_ALL_DRIVES", env)
        self.assertEqual(env["ADAPTER_HOST"], "127.0.0.1")
        self.assertEqual(binds, ADAPTER_BINDS + ["/data/auth/identity-hs256.key:%s:ro"
                                                 % auth_deploy.ADAPTER_KEY])

    def test_enabled_adapter_passes_its_own_startup_check(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "..", "..", "adapter"))
        import integration
        env, _ = auth_deploy.adapter_change(ADAPTER_ENV, ADAPTER_BINDS, "/data/auth", {}, True)
        self.assertEqual(integration.from_env(env), integration.MODE_STANDALONE)

    def test_ui_gets_the_gate_settings_and_only_the_service_key(self):
        env, binds = auth_deploy.ui_change(UI_ENV, UI_BINDS, "/data/auth", 8097, True)
        self.assertEqual(env["ADAPTER_INTEGRATION"], "standalone")
        self.assertEqual(env["AUTH_URL"], "http://127.0.0.1:8097")
        self.assertEqual(env["AUTH_SERVICE_KEY_FILE"], auth_deploy.UI_KEY)
        self.assertEqual(binds, UI_BINDS + ["/data/auth/service.key:%s:ro" % auth_deploy.UI_KEY])
        self.assertFalse(any("identity-hs256" in bind for bind in binds))

    def test_enable_twice_changes_nothing(self):
        once = auth_deploy.adapter_change(ADAPTER_ENV, ADAPTER_BINDS, "/data/auth", {}, True)
        twice = auth_deploy.adapter_change(*once, "/data/auth", {}, True)
        self.assertEqual(once, twice)
        ui_once = auth_deploy.ui_change(UI_ENV, UI_BINDS, "/data/auth", 8097, True)
        self.assertEqual(auth_deploy.ui_change(*ui_once, "/data/auth", 8097, True), ui_once)

    def test_disable_restores_the_vivesecbox_configuration(self):
        env, binds = auth_deploy.adapter_change(ADAPTER_ENV, ADAPTER_BINDS, "/data/auth", {}, True)
        self.assertEqual(env["ADAPTER_ACL_RULES"], "/data/acl-rules.json")
        env, binds = auth_deploy.adapter_change(env, binds, "/data/auth", {}, False)
        expected = {key: value for key, value in ADAPTER_ENV.items()
                    if key not in ("ADAPTER_SCOPE_ALL_DRIVES", "ADAPTER_IDENTITY_MODE")}
        self.assertEqual((env, binds), (expected, ADAPTER_BINDS))
        ui_env, ui_binds = auth_deploy.ui_change(UI_ENV, UI_BINDS, "/data/auth", 8097, True)
        self.assertEqual(auth_deploy.ui_change(ui_env, ui_binds, "/data/auth", 8097, False),
                         (UI_ENV, UI_BINDS))

    def test_a_rules_file_set_up_by_hand_is_kept(self):
        own = dict(ADAPTER_ENV, ADAPTER_ACL_RULES="/data/jobs/rules.json")
        env, binds = auth_deploy.adapter_change(own, ADAPTER_BINDS, "/data/auth", {}, True)
        self.assertEqual(env["ADAPTER_ACL_RULES"], "/data/jobs/rules.json")
        env, _ = auth_deploy.adapter_change(env, binds, "/data/auth", {}, False)
        self.assertEqual(env["ADAPTER_ACL_RULES"], "/data/jobs/rules.json")

    def test_the_rules_file_must_live_in_a_writable_mount(self):
        binds = ADAPTER_BINDS + ["/data/auth/identity-hs256.key:/run/aibox-auth/identity-hs256.key:ro"]
        self.assertEqual(auth_deploy.acl_rules_path({}, binds),
                         ("/data/acl-rules.json", "/data/adapter/acl-rules.json"))
        self.assertEqual(auth_deploy.acl_rules_path({"ADAPTER_ACL_RULES": "/data/jobs/r.json"}, binds),
                         ("/data/jobs/r.json", "/data/jobs/r.json"))
        layout = ["/data/adapter:/data/adapter", "/data/jobs:/data/jobs"]
        with self.assertRaises(SystemExit):
            auth_deploy.acl_rules_path({}, layout)
        self.assertEqual(auth_deploy.acl_rules_path({}, layout, "/data/adapter/acl-rules.json")[1],
                         "/data/adapter/acl-rules.json")
        with self.assertRaises(SystemExit):
            auth_deploy.acl_rules_path({}, binds, "/run/aibox-auth/identity-hs256.key")
        with self.assertRaises(SystemExit):
            auth_deploy.acl_rules_path({}, binds, "/data/../etc/x")


class ServiceTest(unittest.TestCase):
    def test_auth_environment_keeps_settings_but_not_paths(self):
        values = auth_deploy.auth_environment(
            {"AUTH_TOKEN_TTL_SECONDS": "300", "AUTH_DATA_DIR": "/x", "PATH": "/bin"},
            {"AUTH_TOTP_ISSUER": "BV AI Box", "AUTH_PORT": "1", "AUTH_JWT_SECRET_FILE": "/tmp/k",
             "HOME": "/root"})
        self.assertEqual(values, {"AUTH_TOKEN_TTL_SECONDS": "300", "AUTH_TOTP_ISSUER": "BV AI Box"})

    def test_image_runs_unprivileged_from_the_service_files(self):
        recipe = auth_deploy.dockerfile("python:3.12-slim", "abc")
        self.assertIn("USER " + auth_deploy.AUTH_UID, recipe)
        self.assertIn('ENTRYPOINT ["python", "/app/auth_service/service.py"]', recipe)
        self.assertLess(recipe.index("COPY"), recipe.index("USER"))

    def test_tests_are_not_shipped(self):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
        names = [path.name for path in auth_deploy.source_files(auth_deploy.Path(root).resolve())]
        self.assertIn("service.py", names)
        self.assertFalse([name for name in names if name.endswith("_test.py")])

    def test_settings_validate_the_data_directory(self):
        self.assertEqual(auth_deploy.settings({})["data_dir"], "/data/auth")
        for bad in ("relative/auth", "/auth", "/data/../etc"):
            with self.assertRaises(SystemExit):
                auth_deploy.settings({"AIBOX_AUTH_DIR": bad})
        with self.assertRaises(SystemExit):
            auth_deploy.settings({"AIBOX_AUTH_PORT": "http"})


if __name__ == "__main__":
    unittest.main()
