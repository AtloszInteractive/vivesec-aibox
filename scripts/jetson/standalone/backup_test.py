"""backup.py: settings, manifest and verification; with AIBOX_STANDALONE_ROOT_TEST=1
(root, Linux, cryptsetup, docker) the whole cycle on throwaway LUKS files:
vault init -> migrate -> backup init-target -> run (twice, paused container)
-> verify -> drill -> restore -> key rotation -> recovery unlock -> erase."""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from contextlib import redirect_stdout

import backup
import kitlib
import vault


class SettingsTest(unittest.TestCase):
    def test_defaults(self):
        cfg = backup.settings({})
        self.assertEqual((cfg["target"], cfg["keep"], cfg["pause"], cfg["time"], cfg["max_age_hours"]),
                         ("LABEL=AIBOX-BACKUP", 14, True, "02:30", 36))
        self.assertEqual(cfg["extra"], ["/etc/aibox/standalone.conf"])

    def test_unknown_pause_means_pause(self):
        cfg = backup.settings({"AIBOX_BACKUP_PAUSE": "sometimes"})
        self.assertTrue(cfg["pause"])
        self.assertIn("sometimes", cfg["note"])

    def test_rejections(self):
        for env in ({"AIBOX_BACKUP_TARGET": "/data/backup.img"},
                    {"AIBOX_BACKUP_TARGET": "/data/vault/b.img", "AIBOX_VAULT_MOUNT": "/data/vault"},
                    {"AIBOX_BACKUP_TARGET": "/data/adapter/b.img", "AIBOX_VAULT_MOUNT": "/data/vault",
                     "AIBOX_VAULT_LINKS": "/data/adapter"},
                    {"AIBOX_BACKUP_TARGET": "/srv/v.img", "AIBOX_VAULT_FILE": "/srv/v.img"},
                    {"AIBOX_BACKUP_TARGET": "sdb1"},
                    {"AIBOX_BACKUP_KEEP": "0"}, {"AIBOX_BACKUP_TIME": "25:00"},
                    {"AIBOX_BACKUP_MAX_AGE_HOURS": "x"}, {"AIBOX_BACKUP_EXTRA": "/data/x"}):
            with self.assertRaises(kitlib.KitError, msg=env):
                backup.settings(env)

    def test_timer_units(self):
        service, timer = backup.timer_units("03:15")
        self.assertIn("OnCalendar=*-*-* 03:15:00", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("backup.py run --if-present", service)
        self.assertIn("After=aibox-vault.service", service)


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def tree(self, name, files):
        root = os.path.join(self.dir, name)
        for rel, data in files.items():
            path = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        return root

    def test_manifest_and_verify(self):
        root = self.tree("a", {"x.txt": b"hello", "sub/y.bin": b"\0" * 1000, "sub/deep/z": b"z"})
        m = backup.build_manifest(root)
        self.assertEqual([e["p"] for e in m["files"]], ["sub/deep/z", "sub/y.bin", "x.txt"])
        self.assertEqual((m["count"], m["bytes"], m["new_bytes"]), (3, 1006, 1006))
        self.assertEqual(backup.verify_tree(root, m), [])
        with open(os.path.join(root, "x.txt"), "wb") as f:
            f.write(b"HELLO")
        self.assertEqual(backup.verify_tree(root, m, quick=True), [])
        self.assertEqual(backup.verify_tree(root, m), ["content x.txt"])
        os.remove(os.path.join(root, "sub", "deep", "z"))
        self.assertIn("missing sub/deep/z", backup.verify_tree(root, m))
        with open(os.path.join(root, "sub", "y.bin"), "ab") as f:
            f.write(b"!")
        self.assertIn("size sub/y.bin", backup.verify_tree(root, m, quick=True))

    @unittest.skipUnless(hasattr(os, "link") and os.name == "posix", "hard links")
    def test_unchanged_hard_links_reuse_hashes(self):
        prev = self.tree("g1", {"same": b"same", "changed": b"old"})
        m1 = backup.build_manifest(prev)
        cur = self.tree("g2", {"changed": b"new!"})
        os.link(os.path.join(prev, "same"), os.path.join(cur, "same"))
        os.symlink("same", os.path.join(cur, "alias"))
        m2 = backup.build_manifest(cur, prev, m1)
        self.assertEqual(m2["new_bytes"], 4)
        self.assertEqual({e["p"]: e.get("l") for e in m2["files"] if "l" in e}, {"alias": "same"})
        self.assertEqual(backup.verify_tree(cur, m2), [])
        # a reused hash that no longer matches is caught by verify (bit rot on the disk)
        m2["files"] = [dict(e, h="0" * 64) if e["p"] == "same" else e for e in m2["files"]]
        self.assertEqual(backup.verify_tree(cur, m2), ["content same"])

    def test_generation_names(self):
        base = os.path.join(self.dir, "gens")
        for g in ("20261112-010000", "20261113-010000", "20261114-010000.partial", "junk"):
            os.makedirs(os.path.join(base, g, "meta"))
        for g in ("20261112-010000", "20261113-010000"):
            open(os.path.join(base, g, "meta", "manifest.json"), "w").close()
        self.assertEqual(backup.generations(base), ["20261112-010000", "20261113-010000"])
        self.assertEqual(backup.pick(base, "latest"), "20261113-010000")
        with self.assertRaises(kitlib.KitError):
            backup.pick(base, "20261114-010000")
        with self.assertRaises(kitlib.KitError):
            backup.host_base(self.dir, "../etc")


ROOT_TEST = (os.environ.get("AIBOX_STANDALONE_ROOT_TEST") == "1" and sys.platform.startswith("linux")
             and hasattr(os, "geteuid") and os.geteuid() == 0)


@unittest.skipUnless(ROOT_TEST, "set AIBOX_STANDALONE_ROOT_TEST=1 and run as root (it formats LUKS files)")
class RootCycleTest(unittest.TestCase):
    IMAGE = os.environ.get("AIBOX_STANDALONE_TEST_IMAGE", "nginx:1.28-alpine")

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aibox-s8-", dir="/var/tmp")
        self.name = "s8t%d" % os.getpid()
        self.container = "aibox-s8-test-%d" % os.getpid()
        d = self.dir
        os.makedirs(d + "/root/app")
        with open(d + "/root/app/doc.txt", "w") as f:
            f.write("original document\n")
        self.env = {"AIBOX_VAULT_FILE": d + "/vault.img", "AIBOX_VAULT_SIZE": "1G",
                    "AIBOX_VAULT_MOUNT": d + "/root/vault", "AIBOX_VAULT_NAME": self.name,
                    "AIBOX_VAULT_LINKS": d + "/root/app", "AIBOX_VAULT_CONTAINERS": self.container,
                    "AIBOX_VAULT_UNLOCK": "keyfile", "AIBOX_VAULT_KEYFILE": d + "/keys/vault.key",
                    "AIBOX_BACKUP_TARGET": d + "/backup.img", "AIBOX_BACKUP_KEEP": "2",
                    "AIBOX_BACKUP_EXTRA": d + "/standalone.conf"}
        with open(d + "/standalone.conf", "w") as f:
            f.write("AIBOX_VAULT_UNLOCK=keyfile\n")
        self.cfg = backup.settings(self.env)
        self.cfg["vault"]["pbkdf"] = ["--pbkdf", "pbkdf2", "--pbkdf-force-iterations", "1000"]
        self.v = self.cfg["vault"]
        self.recovery_file = d + "/recovery.txt"
        # prepare_target keeps 20 GiB free; the test volume is small
        self.free = shutil.disk_usage("/var/tmp").free
        if self.free < (24 << 30):
            self.skipTest("needs 24 GiB free in /var/tmp")

    def tearDown(self):
        subprocess.run(["docker", "rm", "-f", self.container], capture_output=True)
        for m in (self.v["mount"],):
            subprocess.run(["umount", m], capture_output=True)
        for name in (self.name, backup.MAPPER):
            if os.path.exists("/dev/mapper/" + name):
                subprocess.run(["cryptsetup", "close", name], capture_output=True)
        subprocess.run(["chattr", "-i", self.v["mount"]], capture_output=True)
        shutil.rmtree(self.dir, ignore_errors=True)

    def recovery(self):
        with open(self.recovery_file) as f:
            return vault.normalize_recovery(f.read()).encode()

    def stdin(self, data):
        return mock.patch.object(vault, "stdin_key", lambda: data)

    def read(self, rel):
        with open(os.path.join(self.dir, "root", "app", rel)) as f:
            return f.read()

    def test_full_cycle(self):
        v, cfg = self.v, self.cfg
        quiet = io.StringIO()
        with redirect_stdout(quiet):
            vault.cmd_init(v, no_confirm=True, recovery_out=self.recovery_file)
        info = vault.luks_info(v)
        self.assertEqual({r: x["method"] for r, x in info["roles"].items()},
                         {"unlock": "keyfile", "recovery": "recovery-key"})
        self.assertEqual(os.stat(v["keyfile"]).st_mode & 0o777, 0o400)
        self.assertTrue(vault.is_mounted(v))

        subprocess.run(["docker", "create", "--name", self.container, "--restart", "unless-stopped",
                        "--init", "-v", self.dir + "/root/app:/d", self.IMAGE, "sh", "-c",
                        "trap 'exit 0' TERM; while true; do date +%s%N > /d/tick; sleep 0.2; done"], check=True,
                       capture_output=True)
        vault.cmd_migrate(v)
        self.assertEqual(vault.link_state(v)[0]["state"], "linked")
        self.assertEqual(self.read("doc.txt"), "original document\n")
        self.assertEqual(len(vault.plaintext_copies(v)), 1)
        time.sleep(1)
        self.assertTrue(os.path.exists(os.path.join(v["mount"], "app", "tick")))

        # lock: container stopped, mount point immutable and empty, docker cannot write to it
        vault.cmd_lock(v)
        self.assertFalse(vault.is_open(v))
        self.assertTrue(vault.immutable(v["mount"]))
        self.assertEqual(os.listdir(v["mount"]), [])
        started = subprocess.run(["docker", "start", self.container], capture_output=True)
        self.assertNotEqual(started.returncode, 0)
        self.assertEqual(os.listdir(v["mount"]), [])
        # boot (keyfile) unlocks and starts the container
        vault.cmd_boot(v)
        self.assertTrue(vault.is_mounted(v))
        self.assertTrue(kitlib.docker_inspect([self.container])[0]["State"]["Running"])

        # backup disk: wrong recovery key refused, right one accepted
        with self.stdin(b"0000-0000-0000-0000-0000-0000-0000-0000"):
            with self.assertRaises(kitlib.KitError):
                backup.cmd_init_target(cfg, cfg["target"], size="1G", recovery_stdin=True)
        self.assertFalse(os.path.exists(cfg["target"]))
        with self.stdin(self.recovery()):
            backup.cmd_init_target(cfg, cfg["target"], size="1G", recovery_stdin=True)
        self.assertEqual(os.stat(backup.key_path(v)).st_mode & 0o777, 0o400)

        backup.cmd_run(cfg)
        with open(os.path.join(v["mount"], "app", "doc.txt"), "a") as f:
            f.write("second version\n")
        time.sleep(1.1)
        backup.cmd_run(cfg)
        self.assertTrue(kitlib.docker_inspect([self.container])[0]["State"]["Running"])
        self.assertFalse(kitlib.docker_inspect([self.container])[0]["State"]["Paused"])
        status = backup.load_status(v)
        self.assertEqual(status["last_ok"]["result"], "ok")
        self.assertLess(status["last_ok"]["new_bytes"], status["last_ok"]["bytes"])
        self.assertEqual(backup.cmd_status(cfg), 0)
        backup.cmd_verify(cfg)
        with redirect_stdout(quiet):
            backup.cmd_list(cfg)
        time.sleep(1.1)
        backup.cmd_run(cfg)
        with redirect_stdout(quiet):
            backup.cmd_list(cfg)
        self.assertEqual(len([line for line in quiet.getvalue().splitlines() if line.startswith("  2")]), 2 + 2)

        backup.cmd_drill(cfg)
        self.assertEqual(backup.load_status(v)["last_drill"]["result"], "ok")
        self.assertFalse([n for n in os.listdir(backup.meta_dir(v)) if n.startswith("drill-")])

        # restore an older state over a damaged volume
        os.remove(os.path.join(v["mount"], "app", "doc.txt"))
        with open(os.path.join(v["mount"], "app", "stray"), "w") as f:
            f.write("x")
        with self.assertRaises(kitlib.KitError):
            backup.cmd_restore(cfg, confirm="wrong-host")
        backup.cmd_restore(cfg, confirm=__import__("socket").gethostname())
        self.assertEqual(self.read("doc.txt"), "original document\nsecond version\n")
        self.assertFalse(os.path.exists(os.path.join(v["mount"], "app", "stray")))
        self.assertTrue(kitlib.docker_inspect([self.container])[0]["State"]["Running"])

        # the recovery key opens the backup disk too
        with self.stdin(self.recovery()):
            backup.cmd_verify(cfg, quick=True, recovery=True, key_stdin=True)

        # rotate the unlock key: the old key file stops working, the new one opens
        old_key = vault.read_key_file(v["keyfile"])
        vault.cmd_rotate(v, "unlock")
        new_key = vault.read_key_file(v["keyfile"])
        self.assertNotEqual(old_key, new_key)
        self.assertFalse(vault.test_key(v, old_key))
        self.assertTrue(vault.test_key(v, new_key))
        # rotate the recovery key, authorized by the old recovery key
        old_recovery = self.recovery()
        os.remove(self.recovery_file)
        with self.stdin(old_recovery), redirect_stdout(quiet):
            vault.cmd_rotate(v, "recovery", key_stdin=True, recovery_out=self.recovery_file)
        self.assertFalse(vault.test_key(v, old_recovery))
        self.assertTrue(vault.test_key(v, self.recovery()))
        # the backup disk still holds the old recovery key: refused until rekeyed
        disk = backup.disk_cfg(v, cfg["target"])
        self.assertTrue(vault.test_key(disk, old_recovery))
        with self.assertRaises(kitlib.KitError) as stale:
            backup.cmd_run(cfg)
        self.assertIn("previous recovery key", str(stale.exception))
        with self.stdin(old_recovery):
            with self.assertRaises(kitlib.KitError):
                backup.cmd_rekey(cfg, recovery_stdin=True)
        with self.stdin(self.recovery()):
            backup.cmd_rekey(cfg, recovery_stdin=True)
        self.assertFalse(vault.test_key(disk, old_recovery))
        self.assertTrue(vault.test_key(disk, self.recovery()))
        time.sleep(1.1)
        backup.cmd_run(cfg)
        self.assertEqual(sorted(vault.luks_info(v)["keyslots"]), sorted(
            x["keyslot"] for x in vault.luks_info(v)["roles"].values()))

        # unlock with the recovery key after a lock
        vault.cmd_lock(v)
        with self.stdin(self.recovery()):
            vault.cmd_unlock(v, recovery=True, key_stdin=True)
        self.assertEqual(self.read("doc.txt"), "original document\nsecond version\n")
        with self.stdin(b"wrong passphrase!!"):
            vault.cmd_lock(v)
            with self.assertRaises(kitlib.KitError):
                vault.cmd_unlock(v, key_stdin=True)

        # header backup refuses the volume itself; erase destroys every key
        with self.assertRaises(kitlib.KitError):
            vault.cmd_header_backup(v, os.path.join(v["mount"], "h.img"))
        vault.cmd_header_backup(v, self.dir + "/header.img")
        with self.assertRaises(kitlib.KitError):
            vault.cmd_erase(v, "nope")
        vault.cmd_erase(v, __import__("socket").gethostname())
        self.assertFalse(vault.test_key(v, new_key))
        self.assertFalse(os.path.exists(v["keyfile"]))
        self.assertEqual(vault.luks_info(v)["keyslots"], [])


if __name__ == "__main__":
    unittest.main()
