import unittest

import dossier


class FakeGraph:
    def __init__(self, node, documents):
        self.node, self.documents = node, documents

    def post(self, path, body):
        if path == "/query/neighborhood":
            return {"nodes": [self.node], "edges": [], "documents": self.documents}
        return {"profile": {}}


def attr(name, value, doc_id, page=1):
    return {"name": name, "value": value, "doc_id": doc_id, "page": page}


class DossierAttributesTest(unittest.TestCase):
    def test_a_value_many_documents_state_is_one_line_with_their_sources(self):
        documents = {d: {"source_path": "/storage/drives/a/%s.pdf" % d} for d in ("d1", "d2", "d3")}
        documents["hidden"] = {"source_path": "/storage/drives/secret/x.pdf"}
        node = {"id": "p", "type": "person", "subtype": "", "label": "Antal Lajos", "aliases": [],
                "mentions": 3, "documents": list(documents),
                "attributes": [attr("registry_number", "NY-104272", "d1"),
                               attr("registry_number", "NY-104272 ", "d2"),
                               attr("registry_number", "NY-104272", "d3", page=4),
                               attr("registry_number", "NY-999999", "hidden"),
                               attr("birth_date", "1999-01-01", "d1"),
                               attr("birth_date", "1999-02-02", "d2")]}
        text, _ = dossier.build(FakeGraph(node, documents), {}, lambda path: "/secret/" not in path,
                                "p", lang="hu")
        lines = [line for line in text.splitlines() if line.startswith("- ")]
        self.assertEqual(["- Nyilvántartási szám: NY-104272 [1][2][3]"],
                         [line for line in lines if "Nyilvántartási" in line])
        # Different values stay apart; a hidden document's value is not there at all.
        self.assertEqual(2, sum("Születési dátum" in line for line in lines))
        self.assertNotIn("NY-999999", text)
        self.assertIn("[3] /storage/drives/a/d3.pdf (o. 4)", text)


if __name__ == "__main__":
    unittest.main()
