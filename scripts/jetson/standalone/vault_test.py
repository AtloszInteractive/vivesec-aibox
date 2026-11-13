"""vault.py: settings, keys, header parsing, container selection, host checks.

The cryptsetup part runs against a real LUKS2 file when cryptsetup is
installed (Linux, no root needed); the root-only steps (open, mount, guard,
migrate) were exercised on the dev box (see the standalone README).
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import kitlib
import vault

DUMP = """LUKS header information
Version:       \t2
Epoch:         \t6
UUID:          \t40bf4c10-3ca6-4553-8808-b33ccd030045
Label:         \taibox-vault
Subsystem:     \t(no subsystem)

Data segments:
  0: crypt
\toffset: 16777216 [bytes]

Keyslots:
  0: luks2
\tKey:        512 bits
\tAF hash:    sha256
  1: luks2
\tKey:        512 bits
  3: luks2
\tKey:        512 bits
Tokens:
  0: aibox-role
\tKeyslot:  0
  2: aibox-role
\tKeyslot:  3
Digests:
  0: pbkdf2
\tHash:       sha256
"""


def container(name, sources, policy="unless-stopped", readonly=False, tmpfs=None):
    return {"Name": "/" + name,
            "HostConfig": {"RestartPolicy": {"Name": policy}, "ReadonlyRootfs": readonly,
                           "Tmpfs": tmpfs},
            "Mounts": [{"Type": "bind", "Source": s} for s in sources]}


class SettingsTest(unittest.TestCase):
    def test_defaults(self):
        cfg = vault.settings({})
        self.assertEqual(cfg["mount"], "/data")
        self.assertEqual(cfg["unlock"], "passphrase")
        self.assertEqual(cfg["links"], [])
        self.assertEqual(cfg["name"], "aibox_vault")
        self.assertEqual(cfg["usb"], "LABEL=AIBOX-KEY")
        with self.assertRaises(kitlib.KitError):
            vault.target(cfg)

    def test_unknown_unlock_mode_falls_back_to_passphrase(self):
        cfg = vault.settings({"AIBOX_VAULT_UNLOCK": "tpm2"})
        self.assertEqual(cfg["unlock"], "passphrase")
        self.assertIn("tpm2", cfg["unlock_note"])

    def test_links_and_rejections(self):
        cfg = vault.settings({"AIBOX_VAULT_MOUNT": "/data/vault",
                              "AIBOX_VAULT_LINKS": "/data/adapter /data/auth,/home/aibox/graph-secrets",
                              "AIBOX_VAULT_FILE": "/var/lib/aibox/vault.img"})
        self.assertEqual(cfg["links"], ["/data/adapter", "/data/auth", "/home/aibox/graph-secrets"])
        bad = [
            {"AIBOX_VAULT_DEVICE": "/dev/nvme0n1p2", "AIBOX_VAULT_FILE": "/x/v.img"},
            {"AIBOX_VAULT_DEVICE": "/tmp/disk"},
            {"AIBOX_VAULT_MOUNT": "/"},
            {"AIBOX_VAULT_MOUNT": "/etc"},
            {"AIBOX_VAULT_MOUNT": "/data/../etc"},
            {"AIBOX_VAULT_MOUNT": "data"},
            {"AIBOX_VAULT_MOUNT": "/data/vault", "AIBOX_VAULT_LINKS": "/data"},
            {"AIBOX_VAULT_MOUNT": "/data/vault", "AIBOX_VAULT_LINKS": "/data/vault/x"},
            {"AIBOX_VAULT_LINKS": "/data/a /srv/a"},
            {"AIBOX_VAULT_LINKS": "/data/.aibox"},
            {"AIBOX_VAULT_LINKS": "/srv"},
            {"AIBOX_VAULT_FILE": "/data/vault.img"},
            {"AIBOX_VAULT_NAME": "Bad-Name"},
            {"AIBOX_VAULT_USB": "/dev/sdb1"},
            {"AIBOX_VAULT_USB_FILE": "../key"},
            {"AIBOX_VAULT_USB_WAIT": "x"},
            {"AIBOX_VAULT_CONTAINERS": "ok bad;name"},
        ]
        for env in bad:
            with self.assertRaises(kitlib.KitError, msg=env):
                vault.settings(env)

    def test_config_file_with_environment_override(self):
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, "standalone.conf")
            with open(conf, "w") as f:
                f.write('# comment\nAIBOX_VAULT_MOUNT="/data/vault"\nexport AIBOX_VAULT_UNLOCK=usb # stick\n'
                        "OTHER=ignored\nAIBOX_VAULT_LINKS='/data/a /data/b'\n")
            values = kitlib.load_settings({"AIBOX_STANDALONE_CONF": conf, "AIBOX_VAULT_UNLOCK": "keyfile"})
        cfg = vault.settings(values)
        self.assertEqual((cfg["mount"], cfg["unlock"], cfg["links"]),
                         ("/data/vault", "keyfile", ["/data/a", "/data/b"]))
        self.assertNotIn("OTHER", values)


class KeyTest(unittest.TestCase):
    def test_recovery_key_round_trip(self):
        keys = {vault.new_recovery_key() for _ in range(50)}
        self.assertEqual(len(keys), 50)
        for key in keys:
            self.assertEqual(len(key), 32)
            shown = vault.format_recovery(key)
            self.assertEqual(len(shown.split("-")), 8)
            self.assertEqual(vault.normalize_recovery(shown.lower().replace("-", " ")), key)

    def test_recovery_typos_map_and_bad_input_fails(self):
        key = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
        self.assertEqual(vault.normalize_recovery("O123-4567-89ab-cdef-ghjk-mnpq-rstv-wxyz"), key)
        self.assertEqual(vault.normalize_recovery("0L23456789ABCDEFGHJKMNPQRSTVWXYZ"), "01" + key[2:])
        for bad in ("", "short", key + "0", key[:-1] + "U"):
            with self.assertRaises(kitlib.KitError):
                vault.normalize_recovery(bad)

    def test_passphrase_rules(self):
        self.assertEqual(vault.check_passphrase("correct horse battery"), b"correct horse battery")
        for bad in ("short", "aaaaaaaaaaaaaaaa", "abababababababab"):
            with self.assertRaises(kitlib.KitError):
                vault.check_passphrase(bad)

    def test_keyfile_key_is_random_and_printable(self):
        a, b = vault.new_keyfile_key(), vault.new_keyfile_key()
        self.assertNotEqual(a, b)
        self.assertEqual(len(a), 64)
        a.decode("ascii")

    def test_recovery_key_never_on_stdout_or_volume(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(kitlib.KitError):
                vault.show_recovery("A" * 32, False, os.path.join(d, "vol", "rk.txt"), mount=os.path.join(d, "vol"))
            out = os.path.join(d, "rk.txt")
            vault.show_recovery("0123456789ABCDEFGHJKMNPQRSTVWXYZ", False, out, mount=os.path.join(d, "vol"))
            with open(out) as f:
                self.assertEqual(f.read().strip(), "0123-4567-89AB-CDEF-GHJK-MNPQ-RSTV-WXYZ")
            if os.name == "posix":
                self.assertEqual(os.stat(out).st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                vault.show_recovery("A" * 32, False, out, mount=os.path.join(d, "vol"))


class HeaderTest(unittest.TestCase):
    def test_parse_dump(self):
        info = vault.parse_dump(DUMP)
        self.assertEqual(info["version"], "2")
        self.assertEqual(info["uuid"], "40bf4c10-3ca6-4553-8808-b33ccd030045")
        self.assertEqual(info["label"], "aibox-vault")
        self.assertEqual(info["keyslots"], [0, 1, 3])
        self.assertEqual(info["tokens"], {0: {"type": "aibox-role", "keyslots": [0]},
                                          2: {"type": "aibox-role", "keyslots": [3]}})
        self.assertEqual(vault.free_slot(info), 2)

    def test_role_token(self):
        import json
        token = json.loads(vault.role_token("recovery", 1, "recovery-key"))
        self.assertEqual((token["type"], token["keyslots"], token["role"]),
                         ("aibox-role", ["1"], "recovery"))


class ContainerTest(unittest.TestCase):
    def test_dependent_containers_by_mount_and_policy(self):
        cfg = vault.settings({"AIBOX_VAULT_MOUNT": "/data/vault",
                              "AIBOX_VAULT_LINKS": "/data/adapter /data/auth /data/rag"})
        containers = [
            container("vivesec-ui", ["/home/aibox/build/.output", "/data/auth/service.key"]),
            container("vivesec-adapter", ["/data/adapter", "/data/jobs"]),
            container("vivesec-adapter-prev-20261112", ["/data/adapter"]),
            container("vivesec-rag", ["/data/rag"]),
            container("vivesec-rag-eval", ["/data/rag-eval"]),
            container("colearn", ["/data/docker/volumes/x"]),
            container("oneshot", ["/data/adapter"], policy="no"),
            container("vivesec-https", ["/data/vault/tls/server"]),
            container("other-tool", ["/data/vault/x"]),
        ]
        self.assertEqual(vault.dependent_containers(cfg, containers),
                         ["vivesec-rag", "other-tool", "vivesec-adapter", "vivesec-ui", "vivesec-https"])

    def test_explicit_list_wins(self):
        cfg = vault.settings({"AIBOX_VAULT_CONTAINERS": "vivesec-ui vivesec-rag"})
        containers = [container("vivesec-ui", []), container("vivesec-rag", []),
                      container("vivesec-adapter", ["/data/adapter"])]
        self.assertEqual(vault.dependent_containers(cfg, containers), ["vivesec-rag", "vivesec-ui"])

    def test_fresh_layout_mount_is_data(self):
        cfg = vault.settings({})
        containers = [container("vivesec-rag", ["/data/rag"]), container("tool", ["/srv/x"])]
        self.assertEqual(vault.dependent_containers(cfg, containers), ["vivesec-rag"])


class HostCheckTest(unittest.TestCase):
    def fake_run(self, swaps="", core="core", lsblk="crypt"):
        def run(args, stdin=None, check=True, timeout=None):
            if args[0] == "swapon":
                return 0, swaps, ""
            if args[0] == "lsblk":
                return 0, lsblk + "\n", ""
            if args[0] == "lsattr":
                return 0, "----i---------e----- /x\n", ""
            if args[0] == "docker":
                raise kitlib.KitError("no docker")
            return 0, "", ""
        return run

    def checks(self, **kw):
        cfg = vault.settings({"AIBOX_VAULT_MOUNT": "/nonexistent-vault-mount"})
        with mock.patch.object(vault, "run", self.fake_run(**kw)), \
                mock.patch.object(kitlib, "run", self.fake_run(**kw)):
            return vault.host_checks(cfg)

    def test_plaintext_swap_fails_zram_and_crypt_pass(self):
        self.assertIn("FAIL", [lvl for lvl, msg in self.checks(swaps="/swap.img file\n") if "swap" in msg])
        ok = self.checks(swaps="/dev/zram0 partition\n/dev/mapper/cswap partition\n")
        self.assertEqual([lvl for lvl, msg in ok if "swap" in msg], ["PASS"])
        bad = self.checks(swaps="/dev/mapper/plain partition\n", lsblk="lvm")
        self.assertEqual([lvl for lvl, msg in bad if "swap" in msg], ["FAIL"])

    def test_unguarded_mount_point_fails(self):
        levels = [lvl for lvl, msg in self.checks() if "mount point" in msg]
        self.assertEqual(levels, ["FAIL"])


class UnitTest(unittest.TestCase):
    def test_unit_unlocks_after_docker_and_locks_at_shutdown(self):
        text = vault.unit_text()
        self.assertIn("After=docker.service", text)
        self.assertIn("vault.py boot", text)
        self.assertIn("vault.py lock --shutdown", text)
        self.assertIn("RemainAfterExit=yes", text)


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("cryptsetup"),
                     "needs cryptsetup (Linux)")
class CryptsetupTest(unittest.TestCase):
    """Real LUKS2 header on a file: format, recovery slot, role tokens, keys."""

    def test_format_roles_and_keys(self):
        with tempfile.TemporaryDirectory() as d:
            img = os.path.join(d, "v.img")
            with open(img, "wb") as f:
                f.truncate(32 << 20)
            cfg = vault.settings({"AIBOX_VAULT_FILE": img, "AIBOX_VAULT_MOUNT": os.path.join(d, "m")})
            cfg["pbkdf"] = ["--pbkdf", "pbkdf2", "--pbkdf-force-iterations", "1000"]
            unlock, recovery = vault.new_keyfile_key(), vault.new_recovery_key().encode()
            vault.cs(["-q", "luksFormat"] + vault.LUKS_FORMAT + cfg["pbkdf"]
                     + ["--key-slot", "0", "--key-file", "-", img], key=unlock)
            vault.cs(["luksAddKey", "--key-slot", "1", "--key-file", "-"] + cfg["pbkdf"] + [img, "{NEWKEY}"],
                     key=unlock, new_key=recovery)
            vault.set_role(cfg, vault.luks_info(cfg), "unlock", 0, "usb")
            vault.set_role(cfg, vault.luks_info(cfg), "recovery", 1, "recovery-key")
            info = vault.luks_info(cfg)
            self.assertEqual(info["keyslots"], [0, 1])
            self.assertEqual({r: (v["keyslot"], v["method"]) for r, v in info["roles"].items()},
                             {"unlock": (0, "usb"), "recovery": (1, "recovery-key")})
            self.assertTrue(vault.test_key(cfg, unlock, 0))
            self.assertTrue(vault.test_key(cfg, recovery, 1))
            self.assertFalse(vault.test_key(cfg, recovery, 0))
            self.assertFalse(vault.test_key(cfg, b"wrong"))
            # a re-recorded role replaces its token
            vault.set_role(cfg, info, "unlock", 0, "passphrase")
            self.assertEqual(vault.luks_info(cfg)["roles"]["unlock"]["method"], "passphrase")
            self.assertEqual(len(vault.luks_info(cfg)["tokens"]), 2)


if __name__ == "__main__":
    unittest.main()
