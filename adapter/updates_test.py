import json
import os
import tempfile
import unittest

import updates


def write_state(directory, **state):
    with open(os.path.join(directory, "state.json"), "w", encoding="utf-8") as handle:
        json.dump(state, handle)


READY = {"status": "ready", "installed": "26.10.1-dev+4d28a33",
         "available": {"version": "26.11.1", "notes": {"hu": "Javítások", "xx": "?"}, "security": "yes",
                       "compat": {"snapshot": True}, "download_bytes": 123,
                       "files": {"kit": "/var/lib/vivesec-matrix/updater/targets/x"}}}


class UpdateChannelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.now = [1000.0]
        self.channel = updates.UpdateChannel(self.dir, clock=lambda: self.now[0])

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_page_sees_only_known_fields(self):
        write_state(self.dir, **READY, secret="x")
        state = self.channel.state()
        self.assertEqual(state["available"]["notes"], {"hu": "Javítások"})
        self.assertIs(state["available"]["security"], False)  # only a real true counts
        self.assertTrue(state["available"]["data_copy"])
        self.assertNotIn("files", json.dumps(state))
        self.assertNotIn("secret", state)
        write_state(self.dir, status="rm -rf", available={"version": "../1"})
        self.assertEqual((self.channel.state()["status"], self.channel.state()["available"]), ("never", None))

    def test_install_only_the_offered_release_and_once(self):
        write_state(self.dir, **dict(READY, status="failed", interrupted=True))
        with self.assertRaises(updates.UpdateRefused) as refused:
            self.channel.request("install", "admin", version="26.11.1")
        self.assertEqual(refused.exception.code, "not_ready")  # interrupted: go back first
        write_state(self.dir, **READY)
        with self.assertRaises(updates.UpdateRefused) as refused:
            self.channel.request("install", "admin", version="26.12.1")
        self.assertEqual(refused.exception.code, "not_ready")
        self.channel.request("install", "admin", version="26.11.1")
        with open(os.path.join(self.dir, "request.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["version"], "26.11.1")
        with self.assertRaises(updates.UpdateRefused) as refused:
            self.channel.request("install", "admin", version="26.11.1")
        self.assertEqual(refused.exception.code, "request_pending")
        self.now[0] += 3600
        self.assertTrue(self.channel.state()["pending"]["stuck"])

    def test_rollback_needs_a_last_update_and_its_data_copy(self):
        write_state(self.dir, status="current")
        self.assertRaises(updates.UpdateRefused, self.channel.request, "rollback", "admin")
        write_state(self.dir, status="current", last={"version": "26.11.1", "previous": {"label": "26.10.1"}})
        with self.assertRaises(updates.UpdateRefused) as refused:
            self.channel.request("rollback", "admin", restore_data=True)
        self.assertEqual(refused.exception.code, "no_data_copy")
        self.assertFalse(self.channel.request("rollback", "admin")["restore_data"])

    def test_after_a_failed_install_only_the_data_copy_or_a_broken_box_justify_going_back(self):
        last = {"version": "26.11.1", "previous": {"label": "26.10.1"}, "snapshot": "/var/lib/x/backups/pre"}
        write_state(self.dir, status="failed", installed="26.10.1", last=last)
        with self.assertRaises(updates.UpdateRefused) as refused:
            self.channel.request("rollback", "admin")
        self.assertEqual(refused.exception.code, "already_previous")
        self.assertTrue(self.channel.request("rollback", "admin", restore_data=True)["restore_data"])
        os.remove(os.path.join(self.dir, "request.json"))
        write_state(self.dir, status="broken", installed="26.10.1", last=last)
        self.assertEqual(self.channel.request("rollback", "admin")["action"], "rollback")

    def test_off_without_a_directory(self):
        channel = updates.UpdateChannel.from_env({"ADAPTER_UPDATES_DIR": " "})
        self.assertFalse(channel.enabled)
        with self.assertRaises(updates.UpdateRefused) as refused:
            channel.request("install", "admin", version="26.11.1")
        self.assertEqual(refused.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
