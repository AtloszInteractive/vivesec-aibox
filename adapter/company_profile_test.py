"""E12 company profile: the facts and their states, history and restore, the
background the model gets up to the caller's clearance, proposals read from
documents (the model is mocked), and the administration endpoints."""
import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import company_extract
import company_profile as cp
import integration
import llm
import orchestrator
import templates
from store_http_test import ROOM, StandaloneHttpCase

TODAY = "2026-11-18"
CONTEXTS = [{"text": "Visit log", "source_path": "/d/a.txt"}]


def fact(**kw):
    return dict({"group": "legal", "field": "name", "label": "", "value": "Büntetés-végrehajtás",
                 "classification": "public"}, **kw)


class StoreCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tpl = templates.TemplateStore(os.path.join(temp.name, "t"))
        self.profile = cp.CompanyProfile(self.tpl)

    def add(self, **kw):
        state = self.profile.save_fact(fact(**kw), "admin", self.profile.state()["revision"])
        return state["facts"][-1]["id"]

    def approve(self, fact_id):
        return self.profile.set_state(fact_id, "effective", "admin",
                                      self.profile.state()["revision"])


class ProfileStoreTest(StoreCase):
    def test_draft_approve_edit_and_replace(self):
        first = self.add()
        self.assertEqual(self.profile.facts()[0]["state"], "draft")
        self.assertEqual(cp.prompt_block(self.profile.facts(), today=TODAY), "")
        self.approve(first)
        self.assertIn("- Name: Büntetés-végrehajtás", cp.prompt_block(self.profile.facts(),
                                                                     today=TODAY))
        # Editing what is in use makes a draft; the old value stays until approval.
        self.profile.save_fact(dict(fact(value="BV Országos Parancsnokság"), id=first), "admin",
                               self.profile.state()["revision"])
        facts = {f["id"]: f for f in self.profile.facts()}
        draft = next(f for f in facts.values() if f["id"] != first)
        self.assertEqual((facts[first]["state"], draft["state"], draft["replaces"]),
                         ("effective", "draft", first))
        self.approve(draft["id"])
        facts = {f["id"]: f for f in self.profile.facts()}
        self.assertEqual((facts[first]["state"], facts[draft["id"]]["state"]),
                         ("expired", "effective"))
        # Another value of a single-valued field expires the effective one.
        third = self.add(value="Harmadik név")
        self.approve(third)
        states = sorted(f["state"] for f in self.profile.facts())
        self.assertEqual(states, ["effective", "expired", "expired"])
        # Repeatable items with different names live side by side.
        for label in ("BV", "FEFO"):
            self.approve(self.add(group="terms", field="term", label=label, value="x"))
        self.assertEqual(sum(1 for f in self.profile.facts() if f["group"] == "terms"
                             and f["state"] == "effective"), 2)

    def test_validation_conflicts_and_deletion(self):
        for bad in (fact(group="legal", field="nope"), fact(value=" \n "),
                    fact(label="x"), fact(group="terms", field="term", label=""),
                    fact(classification="top_secret"), fact(valid_from="2026-02-30"),
                    fact(valid_from="2026-05-01", valid_to="2026-04-01"),
                    fact(value="x" * 2001)):
            with self.assertRaises(cp.ProfileInvalid, msg=bad):
                self.profile.save_fact(bad, "admin", 0)
        first = self.add()
        with self.assertRaises(cp.ProfileConflict):
            self.profile.save_fact(fact(value="y"), "admin", 0)
        self.approve(first)
        with self.assertRaises(cp.ProfileInvalid):
            self.profile.delete(first, "admin", self.profile.state()["revision"])
        self.profile.set_state(first, "archived", "admin", self.profile.state()["revision"])
        self.profile.delete(first, "admin", self.profile.state()["revision"])
        self.assertEqual(self.profile.facts(), [])
        with self.assertRaises(KeyError):
            self.profile.set_state(first, "effective", "admin", self.profile.state()["revision"])

    def test_history_and_restore(self):
        first = self.add(value="Régi név")
        self.profile.save_fact(dict(fact(value="Új név"), id=first), "admin",
                               self.profile.state()["revision"])
        history = self.profile.history(first)
        self.assertEqual([h["action"] for h in history], ["edit", "create"])
        self.assertEqual((history[0]["before"]["value"], history[0]["after"]["value"]),
                         ("Régi név", "Új név"))
        self.profile.restore(first, history[0]["seq"], "anna", self.profile.state()["revision"])
        self.assertEqual(self.profile.facts()[0]["value"], "Régi név")
        self.assertEqual(self.profile.history(first)[0]["user"], "anna")
        # A deleted fact comes back from its history.
        self.profile.delete(first, "admin", self.profile.state()["revision"])
        seq = self.profile.history(first)[-1]["seq"]
        self.profile.restore(first, seq, "admin", self.profile.state()["revision"])
        self.assertEqual(len(self.profile.facts()), 1)

    def test_an_unreadable_profile_gives_no_background(self):
        self.approve(self.add())
        with open(self.profile._path(), "w") as handle:
            handle.write("{broken")
        state = self.profile.state()
        self.assertEqual((state["facts"], bool(state.get("error"))), ([], True))
        with self.assertRaises(cp.ProfileInvalid):
            self.profile.save_fact(fact(), "admin", 0)

    def test_the_card_of_the_first_step_is_taken_over_once(self):
        self.tpl._set("org_card", {"name": "BV", "about": "Börtönök", "terms": [
            {"term": "FEFO", "meaning": "felderítő"}]}, "admin", None)
        self.assertEqual(self.profile.take_over_card(), 3)
        facts = self.profile.facts()
        self.assertEqual({(f["group"], f["field"], f["state"], f["classification"])
                          for f in facts},
                         {("legal", "name", "effective", "public"),
                          ("intro", "short_description", "effective", "public"),
                          ("terms", "term", "effective", "public")})
        self.assertEqual(self.profile.take_over_card(), 0)
        self.assertEqual(len(self.profile.facts()), 3)


class BackgroundTest(unittest.TestCase):
    FACTS = [
        dict(fact(), id="1" * 16, state="effective"),
        dict(fact(group="terms", field="term", label="BV", value="büntetés-végrehajtás"),
             id="2" * 16, state="effective"),
        dict(fact(group="policies", field="policy", label="Látogatási rend",
                  value="Látogatás havonta kétszer\negy órában.", classification="internal"),
             id="3" * 16, state="effective"),
        dict(fact(group="organisation", field="unit", label="Felderítő osztály",
                  value="Vezető: X. Y.", classification="confidential"),
             id="4" * 16, state="effective"),
        dict(fact(group="other", field="note", value="lejárt", valid_to="2026-01-01"),
             id="5" * 16, state="effective"),
        dict(fact(group="other", field="note", value="tervezet"), id="6" * 16, state="draft"),
    ]

    def test_up_to_the_clearance_and_in_effect(self):
        public = cp.prompt_block(self.FACTS, clearance=None, today=TODAY)
        self.assertTrue(public.startswith(cp.HEADER))
        self.assertIn("- Term: BV: büntetés-végrehajtás", public)
        for absent in ("Látogatás", "Felderítő", "lejárt", "tervezet"):
            self.assertNotIn(absent, public)
        internal = cp.prompt_block(self.FACTS, clearance=1, today=TODAY)
        self.assertIn("- Policy: Látogatási rend: Látogatás havonta kétszer egy órában.",
                      internal)
        self.assertNotIn("Felderítő", internal)
        self.assertIn("Felderítő osztály", cp.prompt_block(self.FACTS, clearance=2, today=TODAY))

    def test_core_first_then_what_the_question_names(self):
        facts = self.FACTS[:2] + [
            dict(fact(group="policies", field="policy", label="P%d" % i,
                      value="szabály %d" % i), id="%016d" % (10 + i), state="effective")
            for i in range(30)] + [
            dict(fact(group="policies", field="policy", label="Csomagküldés",
                      value="Csomag havonta egyszer."), id="9" * 16, state="effective")]
        budget = len(cp.HEADER) + 200
        block = cp.prompt_block(facts, "Milyen gyakran küldhető csomag?", 0, TODAY, budget)
        lines = block.splitlines()
        self.assertEqual(lines[1], "- Name: Büntetés-végrehajtás")
        self.assertIn("- Policy: Csomagküldés: Csomag havonta egyszer.", lines)
        self.assertLess(len(lines), 10)
        self.assertLessEqual(len(block), budget)


class ModelPromptTest(unittest.TestCase):
    def setUp(self):
        facts = BackgroundTest.FACTS
        patcher = patch.object(llm, "ORGANISATION",
                               lambda q, c: cp.prompt_block(facts, q, c, TODAY))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_answer_prompt_and_the_planner_get_it_up_to_the_clearance(self):
        with llm.organisation_for("Látogatás?", 1), patch.object(llm, "GENERATE", "on"), \
                patch.object(llm, "_ollama_up", return_value=True), \
                patch.object(llm, "_chat", return_value=("ok", 0, 0)) as chat:
            llm.generate("Who?", CONTEXTS, lang="Hungarian")
            llm.generate("Who?", CONTEXTS, lang="Hungarian", profile="hybrid")
            llm.generate("And?", [], history=[{"role": "user", "content": "x"}],
                         chat_only=True)
            orchestrator.plan("Ki?", [], lang="Hungarian")
        systems = [call[0][0] for call in chat.call_args_list]
        self.assertEqual(len(systems), 4)
        for system in systems:
            self.assertIn(cp.HEADER, system)
            self.assertIn("Látogatási rend", system)
            self.assertNotIn("Felderítő", system)
        grounded, hybrid = systems[0], systems[1]
        # The grounding rules come after the background; the hybrid prompt ends with it.
        self.assertLess(grounded.index(cp.HEADER), grounded.index("STRICT RULES:"))
        self.assertTrue(hybrid.rstrip().endswith("egy órában."), hybrid[-120:])

    def test_outside_an_answer_only_public_facts_and_nothing_leaks_across(self):
        self.assertNotIn("Látogatási", llm.organisation_block())
        self.assertIn("BV", llm.organisation_block())
        seen = []

        def other_thread():
            seen.append(llm.organisation_block())

        with llm.organisation_for("x", 4):
            self.assertIn("Felderítő", llm.organisation_block())
            worker = threading.Thread(target=other_thread)
            worker.start()
            worker.join()
        self.assertNotIn("Felderítő", seen[0])
        self.assertNotIn("Felderítő", llm.organisation_block())

        def broken(question, clearance):
            raise OSError("unreadable")
        with patch.object(llm, "ORGANISATION", broken), patch("sys.stderr"):
            self.assertEqual(llm.organisation_block(), "")


class ProposalTest(unittest.TestCase):
    GROUP = [{"text": "A Büntetés-végrehajtás Országos Parancsnoksága (BVOP) székhelye: "
                      "Budapest, Steindl Imre u. 8.", "page_number": 1},
             {"text": "A Felderítő Osztály vezetője a parancsnoknak jelent. "
                      "FEFO: felderítő főosztály.", "page_number": 2}]

    def test_only_quoted_facts_of_known_fields(self):
        answer = {"facts": [
            {"group": "legal", "field": "seat", "label": "x", "value": "Budapest, Steindl Imre u. 8.",
             "quote": "székhelye: Budapest, Steindl Imre u. 8."},
            {"group": "organisation", "field": "unit", "label": "Felderítő Osztály",
             "value": "A parancsnoknak jelent.",
             "quote": "A Felderítő Osztály vezetője a parancsnoknak jelent."},
            {"group": "terms", "field": "term", "label": "FEFO", "value": "felderítő főosztály",
             "quote": "nincs ilyen mondat"},
            {"group": "legal", "field": "ceo", "value": "X", "quote": "BVOP"},
            {"group": "roles", "field": "role", "label": "", "value": "x",
             "quote": "FEFO: felderítő főosztály."}]}
        out = company_extract.proposals(json.dumps(answer), self.GROUP, "/d/szmsz.pdf",
                                        "confidential")
        self.assertEqual([(f["group"], f["field"], f["label"], f["classification"], s["page"])
                          for f, s in out],
                         [("legal", "seat", "", "confidential", 1),
                          ("organisation", "unit", "Felderítő Osztály", "confidential", 2)])
        self.assertEqual(out[1][1]["path"], "/d/szmsz.pdf")
        self.assertEqual(company_extract.proposals("not json", self.GROUP, "/d", "public"), [])
        self.assertIn("organisation/unit", company_extract.system_prompt())


class CompanyHttpTest(StandaloneHttpCase):
    def setUp(self):
        super().setUp()
        self.tpl = templates.TemplateStore(os.path.join(self.dir.name, "templates"))
        self.company = cp.CompanyProfile(self.tpl)
        self.reader = self.service._CompanyReader()
        for name, value in (("TEMPLATES", self.tpl), ("COMPANY", self.company),
                            ("COMPANY_READER", self.reader)):
            patcher = patch.object(self.service, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def send(self, method, path, body=None, **who):
        who.setdefault("drives", ())
        headers = self.headers(**who)
        headers.pop("VVS-Drive")
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, raw, _ = self.request(method, path, data, headers)
        return status, (json.loads(raw.decode("utf-8")) if raw else {})

    def test_only_administrators_in_standalone_mode(self):
        calls = (("GET", "/api/v1/ui/admin/company", None),
                 ("GET", "/api/v1/ui/admin/company/history", None),
                 ("GET", "/api/v1/ui/admin/company/documents", None),
                 ("POST", "/api/v1/ui/admin/company/fact", {"base": 0, "fact": fact()}),
                 ("POST", "/api/v1/ui/admin/company/state", {"base": 0, "id": "x"}),
                 ("POST", "/api/v1/ui/admin/company/delete", {"base": 0, "id": "x"}),
                 ("POST", "/api/v1/ui/admin/company/restore", {"base": 0, "id": "x", "seq": 1}),
                 ("POST", "/api/v1/ui/admin/company/extract", {"paths": ["/x"]}),
                 ("POST", "/api/v1/ui/admin/company/extract/cancel", {}))
        for method, path, body in calls:
            status, answer = self.send(method, path, body, roles=("analyst",))
            self.assertEqual((status, answer.get("error")), (403, "forbidden"), path)
            with patch.object(self.service, "INTEGRATION", integration.MODE_VIVESECBOX):
                status, _ = self.send(method, path, body)
                self.assertEqual(status, 404, path)
        self.assertEqual(self.company.facts(), [])

    def test_facts_through_their_states(self):
        status, body = self.send("GET", "/api/v1/ui/admin/company")
        self.assertEqual((status, body["revision"], body["facts"]), (200, 0, []))
        self.assertEqual(body["schema"][0]["fields"][0], {"id": "name", "label": "Name",
                                                          "repeatable": False, "named": False})
        status, body = self.send("POST", "/api/v1/ui/admin/company/fact",
                                 {"base": 0, "fact": fact(classification="internal")})
        self.assertEqual(status, 200, body)
        fact_id, revision = body["facts"][0]["id"], body["revision"]
        status, body = self.send("POST", "/api/v1/ui/admin/company/state",
                                 {"base": 0, "id": fact_id, "state": "effective"})
        self.assertEqual((status, body["error"]), (409, "profile_changed"))
        status, body = self.send("POST", "/api/v1/ui/admin/company/state",
                                 {"base": revision, "id": fact_id, "state": "effective"})
        self.assertEqual(body["facts"][0]["state"], "effective")
        status, body = self.send("POST", "/api/v1/ui/admin/company/fact",
                                 {"base": body["revision"], "fact": fact(field="nope")})
        self.assertEqual((status, body["error"]), (400, "invalid_profile"))
        status, body = self.send("GET", "/api/v1/ui/admin/company/history?id=" + fact_id)
        self.assertEqual([h["action"] for h in body["history"]], ["state", "create"])
        actions = [e["action"] for e in reversed(self.store.tail_events())]
        self.assertEqual(actions, ["company_fact", "company_state"])

    def test_an_answer_gets_the_facts_up_to_the_callers_clearance(self):
        for level, value in (("internal", "Belső tény"), ("confidential", "Bizalmas tény")):
            fid = self.company.save_fact(fact(group="other", field="note", value=value,
                                              classification=level), "admin",
                                         self.company.state()["revision"])["facts"][-1]["id"]
            self.company.set_state(fid, "effective", "admin", self.company.state()["revision"])
        seen = []
        with patch.object(self.service, "_answer_turn",
                          lambda *a, **k: seen.append(llm.organisation_block())):
            access = self.service.ACL.access_for("anna", None, None)
            self.service._answer("anna", ROOM + "/", "Mi?", 5, None, access=access)
            access.clearance = 2
            self.service._answer("anna", ROOM + "/", "Mi?", 5, None, access=access)
        self.assertIn("Belső tény", seen[0])
        self.assertNotIn("Bizalmas tény", seen[0])
        self.assertIn("Bizalmas tény", seen[1])
        self.assertNotIn("Belső", llm.organisation_block())

    def test_proposals_read_from_documents(self):
        doc = ROOM + "/szmsz.txt"
        self.mirror.upsert(doc, file=True, mtime=1, size=1)
        self.mirror.upsert(ROOM + "/a.txt", file=True, mtime=1, size=1)
        status, body = self.send("GET", "/api/v1/ui/admin/company/documents?q=SZMSZ")
        self.assertEqual(([d["path"] for d in body["documents"]], body["truncated"]),
                         ([doc], False))
        with patch.object(self.service, "MAX_PICKABLE_DOCUMENTS", 1):
            status, body = self.send("GET", "/api/v1/ui/admin/company/documents?q=")
        self.assertEqual(([d["path"] for d in body["documents"]], body["truncated"]),
                         ([ROOM + "/a.txt"], True))
        status, body = self.send("POST", "/api/v1/ui/admin/company/extract",
                                 {"paths": [ROOM + "/missing.txt"]})
        self.assertEqual(status, 404)
        text = "Szervezeti és Működési Szabályzat. A szervezet neve: Kőhegyi Intézet."
        rag = self.rag.post_json

        def post(path, body):
            if path == "/rag/document_context":
                return {"contexts": [{"text": text, "page_number": 1}]}
            return rag(path, body)
        answer = json.dumps({"facts": [{"group": "legal", "field": "name", "label": "",
                                        "value": "Kőhegyi Intézet",
                                        "quote": "A szervezet neve: Kőhegyi Intézet."}]})
        with patch.object(self.service, "rag_post_json", post), \
                patch.object(llm, "_chat", return_value=(answer, 0, 0)):
            status, body = self.send("POST", "/api/v1/ui/admin/company/extract", {"paths": [doc]})
            self.assertEqual(status, 200, body)
            deadline = time.time() + 10
            while self.reader.status()["state"] not in ("done", "failed") \
                    and time.time() < deadline:
                time.sleep(0.05)
        self.assertEqual(self.reader.status()["state"], "done", self.reader.status())
        self.assertEqual(self.reader.status()["proposals"], 1)
        draft = self.company.facts()[0]
        self.assertEqual((draft["state"], draft["value"], draft["source"]["path"],
                          draft["classification"]),
                         ("draft", "Kőhegyi Intézet", doc, "internal"))
        # The same statement read again is not proposed twice.
        with patch.object(self.service, "rag_post_json", post), \
                patch.object(llm, "_chat", return_value=(answer, 0, 0)):
            self.send("POST", "/api/v1/ui/admin/company/extract", {"paths": [doc]})
            deadline = time.time() + 10
            while self.reader.status()["state"] != "done" and time.time() < deadline:
                time.sleep(0.05)
        self.assertEqual(len(self.company.facts()), 1)


if __name__ == "__main__":
    unittest.main()
