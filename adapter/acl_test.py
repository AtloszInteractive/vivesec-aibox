"""E03 access model: deny-first evaluation, inheritance, classification and
the fail-closed edges."""
import json
import os
import tempfile
import time
import unittest

import acl
import directory

ROOT = "/storage/drives/finance"


def info(groups=(), roles=(), source="token", error=None):
    return directory.DirectoryInfo(source, groups, roles, error=error)


class PrincipalTest(unittest.TestCase):
    def test_forms_are_normalized(self):
        self.assertEqual(acl.normalize_principal("Group:ABC-1"), "group:abc-1")
        self.assertEqual(acl.normalize_principal({"type": "role", "id": "Finance.Read"}),
                         "role:finance.read")
        self.assertEqual(acl.normalize_principal("*"), acl.EVERYONE)
        self.assertEqual(acl.normalize_principal("Everyone"), acl.EVERYONE)

    def test_malformed_principals_are_refused(self):
        for value in ("abc", "team:x", "group:", "group:a\x00b", 5, None, {"type": "group"}):
            self.assertIsNone(acl.normalize_principal(value), value)

    def test_levels(self):
        self.assertEqual(acl.parse_level("Strictly-Confidential"), 3)
        self.assertEqual(acl.parse_level("bizalmas"), 2)
        self.assertEqual(acl.parse_level(4), 4)
        self.assertIsNone(acl.parse_level("top secret"))
        self.assertIsNone(acl.parse_level(True))


class FlattenTest(unittest.TestCase):
    def setUp(self):
        self.sync = {}
        self.policy = acl.Policy()

    def effective(self, path):
        return self.policy.effective(path, self.sync.get)

    def allowed(self, path, user="lars", groups=(), roles=(), known=True):
        access = self.policy.access_for(user, None, info(groups, roles) if known else None)
        return acl.decide(self.effective(path), access)[0]

    def test_no_acl_keeps_drive_level_access(self):
        self.assertTrue(self.allowed(ROOT + "/a/b.pdf"))
        self.assertFalse(self.effective(ROOT + "/a/b.pdf").restricted)

    def test_nearest_allow_list_wins(self):
        self.sync[ROOT + "/hr"] = {"allow": ["group:hr"]}
        self.sync[ROOT + "/hr/public"] = {"allow": ["everyone"]}
        self.assertFalse(self.allowed(ROOT + "/hr/salaries.xlsx"))
        self.assertTrue(self.allowed(ROOT + "/hr/salaries.xlsx", groups=["HR"]))
        self.assertTrue(self.allowed(ROOT + "/hr/public/handbook.pdf"))

    def test_deny_wins_on_every_level(self):
        self.sync[ROOT] = {"deny": ["group:interns"]}
        self.sync[ROOT + "/hr/public"] = {"allow": ["everyone"]}
        self.assertFalse(self.allowed(ROOT + "/hr/public/handbook.pdf", groups=["interns"]))
        self.sync[ROOT + "/hr/public/x.pdf"] = {"allow": ["user:lars"], "deny": ["user:lars"]}
        self.assertFalse(self.allowed(ROOT + "/hr/public/x.pdf"))

    def test_inherit_false_without_allow_hides_from_everybody(self):
        self.sync[ROOT + "/vault"] = {"inherit": False}
        self.assertFalse(self.allowed(ROOT + "/vault/a.txt", groups=["anything"]))
        self.sync[ROOT + "/vault/open"] = {"allow": ["user:lars"]}
        self.assertTrue(self.allowed(ROOT + "/vault/open/a.txt"))

    def test_inherit_false_stops_the_parent_allow_but_not_its_deny(self):
        self.sync[ROOT] = {"allow": ["group:finance"], "deny": ["user:eve"]}
        self.sync[ROOT + "/shared"] = {"allow": ["everyone"], "inherit": False}
        self.assertTrue(self.allowed(ROOT + "/shared/a.txt", user="bob"))
        self.assertFalse(self.allowed(ROOT + "/shared/a.txt", user="eve"))

    def test_malformed_acl_denies_the_subtree(self):
        self.sync[ROOT + "/x"] = {"allow": ["nonsense"]}
        self.assertFalse(self.allowed(ROOT + "/x/y/z.txt", groups=["g"]))
        self.sync[ROOT + "/y"] = "not an object"
        self.assertFalse(self.allowed(ROOT + "/y/a.txt"))
        self.sync[ROOT + "/z"] = {"inherit": "yes"}
        self.assertFalse(self.allowed(ROOT + "/z/a.txt"))

    def test_paths_outside_a_drive_are_denied(self):
        for path in ("/etc/passwd", "/storage/drives", ROOT + "/../hr/a.txt", ""):
            self.assertFalse(self.allowed(path), path)

    def test_non_canonical_paths_cannot_dodge_a_folder_acl(self):
        self.sync[ROOT + "/secret"] = {"deny": ["everyone"]}
        self.assertFalse(self.allowed(ROOT + "/secret/x.pdf"))
        for path in (ROOT + "/./secret/x.pdf", ROOT + "//secret/x.pdf",
                     ROOT + "/secret/./x.pdf", "storage/drives/finance/x.pdf"):
            self.assertFalse(self.allowed(path), path)

    def test_unknown_membership_hides_group_denies_and_ignores_group_allows(self):
        self.sync[ROOT + "/a.txt"] = {"deny": ["group:contractors"]}
        self.sync[ROOT + "/b.txt"] = {"allow": ["group:finance"]}
        self.sync[ROOT + "/c.txt"] = {"deny": ["user:eve"]}
        self.assertFalse(self.allowed(ROOT + "/a.txt", known=False))
        self.assertFalse(self.allowed(ROOT + "/b.txt", known=False))
        self.assertTrue(self.allowed(ROOT + "/c.txt", known=False))
        self.assertTrue(self.allowed(ROOT + "/a.txt", known=True))

    def test_directory_error_counts_as_unknown_membership(self):
        access = self.policy.access_for("lars", None, info(["finance"], error="down"))
        self.assertFalse(access.membership_known)
        self.assertNotIn("group:finance", access.principals)

    def test_classification_is_the_highest_level_on_the_path(self):
        self.sync[ROOT + "/board"] = {"classification": "strictly_confidential"}
        self.sync[ROOT + "/board/minutes.pdf"] = {"classification": "public"}
        self.assertEqual(self.effective(ROOT + "/board/minutes.pdf").classification, 3)
        self.assertEqual(self.effective(ROOT + "/other.pdf").classification, 1)
        self.sync[ROOT + "/odd.pdf"] = {"classification": "galactic"}
        self.assertEqual(self.effective(ROOT + "/odd.pdf").classification, acl.TOP_LEVEL)

    def test_user_sees_up_to_the_clearance(self):
        self.sync[ROOT + "/c.pdf"] = {"classification": "confidential"}
        self.assertFalse(self.allowed(ROOT + "/c.pdf"))
        policy = acl.Policy(default_clearance=2)
        access = policy.access_for("lars", None, None)
        self.assertTrue(acl.decide(policy.effective(ROOT + "/c.pdf", self.sync.get), access)[0])

    def test_acl_off_allows_everything(self):
        policy = acl.Policy(mode=acl.MODE_OFF)
        self.sync[ROOT + "/x"] = {"inherit": False}
        access = policy.access_for("lars", None, None)
        self.assertIsNone(access.as_rag())
        self.assertTrue(policy.allows(ROOT + "/x/a", access, self.sync.get))

    def test_as_rag_shape(self):
        self.sync[ROOT + "/hr"] = {"allow": ["Group:HR"], "deny": ["user:Eve"],
                                   "classification": "bizalmas"}
        self.assertEqual(self.effective(ROOT + "/hr/a.txt").as_rag(),
                         {"restricted": True, "allow": ["group:hr"], "deny": ["user:eve"],
                          "classification": 2})

    def test_access_roundtrip_and_malformed_record(self):
        access = self.policy.access_for("Lars", "lars@x.com", info(["G1"], ["R1"]))
        again = acl.Access.from_dict(access.to_dict())
        self.assertEqual(again.principals, access.principals)
        self.assertEqual(again.fingerprint(), access.fingerprint())
        broken = acl.Access.from_dict({"principals": "x", "clearance": "nope"})
        self.assertEqual(broken.clearance, 0)
        self.assertFalse(broken.membership_known)
        self.assertEqual(broken.default_classification, acl.TOP_LEVEL)
        self.assertIsNone(acl.Access.from_dict(None))


class RulesFileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "acl.json")
        self.warnings = []

    def write(self, data):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(data if isinstance(data, str) else json.dumps(data))
        stamp = time.time() + len(self.warnings) + 1
        os.utime(self.path, (stamp, stamp))

    def policy(self):
        return acl.Policy(rules_path=self.path, warn=self.warnings.append)

    def test_rules_and_sync_combine_strictly(self):
        self.write({"paths": {ROOT + "/hr": {"allow": ["group:hr", "group:board"]}}})
        policy = self.policy()
        sync = {ROOT + "/hr": {"allow": ["group:board", "group:audit"]}}
        effective = policy.effective(ROOT + "/hr/a.pdf", sync.get)
        self.assertEqual(effective.allow, ("group:board",))

    def test_room_rule_applies_to_the_whole_drive(self):
        self.write({"paths": {ROOT + "/": {"deny": ["role:intern"]}}})
        policy = self.policy()
        access = policy.access_for("lars", None, info(roles=["Intern"]))
        self.assertFalse(policy.allows(ROOT + "/deep/down/a.txt", access))

    def test_clearance_mapping(self):
        self.write({"clearance": {"default": "public", "group:board": "strictly_confidential",
                                  "role:x": "bogus"},
                    "default_classification": "internal"})
        policy = self.policy()
        self.assertEqual(policy.access_for("lars", None, info()).clearance, 0)
        self.assertEqual(policy.access_for("lars", None, info(["board"])).clearance, 3)
        self.assertEqual(policy.access_for("lars", None, None).clearance, 0)
        self.assertTrue(any("bogus" in w for w in self.warnings))

    def test_unusable_rules_file_denies_everything(self):
        policy = self.policy()
        access = policy.access_for("lars", None, None)
        self.assertFalse(policy.allows(ROOT + "/a.txt", access))
        self.write("{not json")
        self.assertFalse(policy.allows(ROOT + "/a.txt", access))
        self.assertEqual(policy.settings()["rules_error"], "invalid")
        before = policy.fingerprint()
        self.write({"paths": {}})
        self.assertTrue(policy.allows(ROOT + "/a.txt", policy.access_for("lars", None, None)))
        self.assertNotEqual(before, policy.fingerprint())

    def test_explain_names_the_reason(self):
        self.write({"paths": {ROOT + "/hr": {"allow": ["group:hr"]}}})
        policy = self.policy()
        out = policy.explain(ROOT + "/hr/a.pdf", policy.access_for("lars", None, info()))
        self.assertFalse(out["allowed"])
        self.assertEqual(out["reason"], "not in allow list")
        self.assertEqual(out["effective"]["sources"][0]["source"], "rules")


class EnvTest(unittest.TestCase):
    def test_unknown_values_fall_back_to_the_safer_side(self):
        warnings = []
        policy = acl.Policy.from_env({"ADAPTER_ACL": "maybe",
                                      "ADAPTER_ACL_DEFAULT_CLASSIFICATION": "x",
                                      "ADAPTER_ACL_DEFAULT_CLEARANCE": "y"}, warnings.append)
        self.assertEqual(policy.mode, acl.MODE_ENFORCE)
        self.assertEqual(policy.default_classification, acl.TOP_LEVEL)
        self.assertEqual(policy.access_for("u", None, None).clearance, 0)
        self.assertEqual(len(warnings), 3)

    def test_defaults(self):
        policy = acl.Policy.from_env({})
        self.assertTrue(policy.enforced)
        self.assertEqual(policy.default_classification, 1)
        self.assertEqual(policy.access_for("u", None, None).clearance, 1)


if __name__ == "__main__":
    unittest.main()
