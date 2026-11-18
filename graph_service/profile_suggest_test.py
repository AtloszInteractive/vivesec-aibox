"""E26e profile suggestions: grouping what extraction kept as RELATED_TO or
untyped identifiers, within the caller's scope; the model only names, and
apply() gives a validated next profile version."""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain_profile  # noqa: E402
import profile_suggest as ps  # noqa: E402
import service  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402
from model import Document, Entity, Evidence, Mention, Relation  # noqa: E402
from visibility import Scope  # noqa: E402

SECRET = {"restricted": True, "allow": ["user:anna"], "deny": [], "classification": 0}
BELA = {"principals": ["user:bela"], "clearance": 0, "membership_known": True,
        "default_classification": 0}
PROFILE = domain_profile.load("bv")
CURRENT = domain_profile.profile_tag(PROFILE)
LABELS = {"en": "Acquaintance of", "hu": "Ismerőse", "da": "Bekendt af", "de": "Bekannt mit"}


class FakeLLM:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer or {}, error, []

    def json_call(self, system, user, schema, max_tokens=4096):
        self.calls.append((system, user))
        if self.error:
            raise self.error
        return self.answer


def build_store():
    s = MemoryGraphStore()
    n = 0

    def doc(doc_id, links, acl=None, profile=CURRENT, identifiers=()):
        nonlocal n
        entities, mentions, relations = [], [], []
        for label, a_type, b_type in links:
            n += 1
            a, b = "a%d" % n, "b%d" % n
            entities += [Entity(a, a_type, "", "Kiss Ádám %d" % n),
                         Entity(b, b_type, "", "Nagy Béla %d" % n if b_type == "person" else
                                "ABC-%03d" % n)]
            mentions += [Mention("m%da" % n, doc_id, a, "Kiss Ádám %d" % n),
                         Mention("m%db" % n, doc_id, b, "Nagy Béla %d" % n)]
            relations.append(Relation("r%d" % n, "RELATED_TO", a, b, raw_label=label, evidence=[
                Evidence(doc_id, "Kiss Ádám %d %s Nagy Béla %d" % (n, label, n))]))
        for value in identifiers:
            n += 1
            entities.append(Entity("i%d" % n, "identifier", "", value))
            mentions.append(Mention("m%di" % n, doc_id, "i%d" % n, value))
        s.put_document(Document(doc_id, "c1", "/a/%s.pdf" % doc_id, acl=acl, profile=profile),
                       entities, mentions, relations)

    doc("d1", [("ismerőse", "person", "person"), ("has vehicle plate", "person", "identifier")],
        identifiers=["NY-466636 nyilvántartási szám", "+36 70 173 4161 hívószámról", "127."])
    doc("d2", [("Ismerőse", "person", "person"), ("has_vehicle_plate", "person", "identifier")],
        identifiers=["NY-123456", "NY-555555", "104. zárka", "106."])
    doc("d3", [("ismerőse", "person", "person"), ("egyszeri", "person", "person")])
    # Read with an older profile: its label has become a synonym since.
    doc("d4", [("testvére", "person", "person")], profile="bv@6/e5")
    doc("d5", [("testvére", "person", "person")], profile="bv@6/e5")
    # Visible only to anna.
    doc("s1", [("titkos viszony", "person", "person")], acl=SECRET)
    doc("s2", [("titkos viszony", "person", "person")], acl=SECRET)
    return s


ANSWER = {"labels": [{"n": 0, "action": "new", "id": "ACQUAINTANCE_OF", "label": LABELS},
                     {"n": 1, "action": "synonym", "relation": "USES"}],
          "identifiers": [{"n": 0, "action": "new", "id": "registry_number",
                           "label": {"en": "Registry number", "hu": "Nyilvántartási szám",
                                     "da": "Registreringsnummer", "de": "Registernummer"}}]}


class SuggestTest(unittest.TestCase):
    def setUp(self):
        self.store = build_store()
        self.view = self.store.view(Scope(["c1"], BELA))

    def test_groups_what_the_caller_may_see(self):
        llm = FakeLLM(ANSWER)
        out = ps.suggest(self.view, PROFILE, llm)
        self.assertEqual(out["model"], "used")
        items = {i["key"]: i for i in out["items"]}
        self.assertEqual(list(items), ["relation:ismerose", "relation:has vehicle plate",
                                       "identifier:AA-999999"])
        acquaintance = items["relation:ismerose"]
        self.assertEqual((acquaintance["action"], acquaintance["id"], acquaintance["labels"]),
                         ("new", "ACQUAINTANCE_OF", LABELS))
        self.assertEqual((acquaintance["from"], acquaintance["to"]), (["person"], ["person"]))
        self.assertEqual(acquaintance["support"], {"relations": 3, "documents": 3,
                                                   "stale_documents": 0})
        self.assertEqual(acquaintance["variants"], ["ismerőse", "Ismerőse"])
        plate = items["relation:has vehicle plate"]
        self.assertEqual((plate["action"], plate["relation"], plate["fit"]), ("synonym", "USES", 1.0))
        registry = items["identifier:AA-999999"]
        self.assertEqual((registry["action"], registry["id"], registry["support"]),
                         ("new", "registry_number", {"values": 3, "documents": 2}))
        self.assertTrue(re.search(registry["regex"], "NY-466636"))
        # A label the profile already knows: only the documents read before it are reported.
        self.assertEqual([(k["label"], k["relation"], k["support"]["stale_documents"])
                          for k in out["known"]], [("testvére", "RELATIVE_OF", 2)])
        # One document is not a kind of link; a hidden document is not counted at all.
        blob = json.dumps(out, ensure_ascii=False)
        for absent in ("egyszeri", "titkos", "Kiss", "Nagy", "NY-466636", "+36"):
            self.assertNotIn(absent, blob)
        self.assertNotIn("titkos", llm.calls[0][1])
        self.assertIn("NY-466636", llm.calls[0][1])  # examples stay on the box
        anna = self.store.view(Scope(["c1"], dict(BELA, principals=["user:anna"])))
        keys = [i["key"] for i in ps.suggest(anna, PROFILE)["items"]]
        self.assertIn("relation:titkos viszony", keys)

    def test_what_the_model_says_is_checked(self):
        answer = {"labels": [{"n": 0, "action": "synonym", "relation": "NOPE"},
                             {"n": 1, "action": "new", "id": "USES", "label": {"hu": "x"}}],
                  "identifiers": [{"n": 0, "action": "existing", "subtype": "inmate"}]}
        out = ps.suggest(self.view, PROFILE, FakeLLM(answer))
        first, second, third = out["items"]
        self.assertEqual((first["action"], first["id"], first["labels"], first["source"]),
                         ("new", "ISMEROSE", {"en": "ismerőse"}, "rule"))
        self.assertEqual((second["action"], second["id"]), ("new", "HAS_VEHICLE_PLATE"))
        self.assertEqual((third["action"], third["id"]), ("new", "code_aa_999999"))

    def test_without_the_model_every_group_is_offered_to_name(self):
        out = ps.suggest(self.view, PROFILE)
        self.assertEqual(out["model"], "off")
        self.assertEqual({i["source"] for i in out["items"]}, {"rule"})
        out = ps.suggest(self.view, PROFILE, FakeLLM(error=RuntimeError("busy")))
        self.assertEqual((out["model"], len(out["items"])), ("failed", 3))


class ApplyTest(unittest.TestCase):
    def items(self):
        return ps.suggest(build_store().view(Scope(["c1"], BELA)), PROFILE, FakeLLM(ANSWER))["items"]

    def test_accepted_items_make_the_next_version(self):
        profile, changes = ps.apply(PROFILE, self.items())
        self.assertEqual(profile["version"], PROFILE["version"] + 1)
        rels = {r["id"]: r for r in profile["relation_types"]}
        self.assertEqual((rels["ACQUAINTANCE_OF"]["from"], rels["ACQUAINTANCE_OF"]["synonyms"]),
                         (["person"], ["ismerőse"]))
        self.assertEqual(rels["USES"]["synonyms"][-2:], ["has vehicle plate", "has_vehicle_plate"])
        pattern = next(p for p in profile["identifier_patterns"] if p["subtype"] == "registry_number")
        self.assertEqual((pattern["id"], pattern["normalize"]), ("registry_number_pattern", "upper"))
        self.assertIn({"id": "registry_number", "type": "identifier",
                       "label": ANSWER["identifiers"][0]["label"]}, profile["entity_subtypes"])
        self.assertEqual([c["change"] for c in changes], ["relation", "synonyms", "pattern"])
        # The result is a profile like any other: it saves and reloads unchanged.
        self.assertEqual(domain_profile.validate(json.loads(json.dumps(profile))), profile)
        self.assertEqual(PROFILE["version"], domain_profile.load("bv")["version"])

    def test_malformed_items_refuse_the_whole_change(self):
        items = self.items()
        bad = [dict(items[1], relation="NOPE"), dict(items[0], id="USES"),
               dict(items[0], labels={"hu": "x"}), dict(items[0], **{"from": ["thing"]}),
               dict(items[2], regex="("), dict(items[2], action="existing", subtype="inmate"),
               dict(items[0], kind="table")]
        for item in bad:
            with self.assertRaises(domain_profile.ProfileError, msg=item):
                ps.apply(PROFILE, [item])
        with self.assertRaises(domain_profile.ProfileError):
            ps.apply(PROFILE, [dict(items[0], action="ignore")])
        with self.assertRaises(domain_profile.ProfileError):
            ps.apply(PROFILE, [])


class ServiceTest(unittest.TestCase):
    def test_endpoints(self):
        state = service.State(build_store(), PROFILE)
        body = {"corpus_ids": ["c1"], "access": BELA}
        out = service.handle_post(state, "/profile/suggest", body)
        self.assertEqual((out["model"], len(out["items"])), ("off", 3))
        with self.assertRaises(service.HttpError):
            service.handle_post(state, "/profile/suggest", {"access": BELA})
        out = service.handle_post(state, "/profile/suggest/apply", {"items": out["items"][:1]})
        self.assertEqual(out["changes"], [{"change": "relation", "relation": "ISMEROSE"}])
        self.assertEqual(state.profile["version"], PROFILE["version"])  # nothing saved
        with self.assertRaises(service.HttpError) as caught:
            service.handle_post(state, "/profile/suggest/apply", {"items": "x"})
        self.assertEqual(caught.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
