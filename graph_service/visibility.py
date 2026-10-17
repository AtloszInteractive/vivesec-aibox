"""Who may see what: the caller's scope applied to graph documents (E26).

The adapter decides the scope per request (allowed corpora + flattened access
filter) and sends it with every query. The graph never stores its own copy of
the rights; it reuses the document rule of rag_service/access.py so both
services decide identically.
"""
from __future__ import annotations

import importlib.util
import os

# Loaded by file path: putting rag_service on sys.path would shadow modules here.
_ACCESS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "rag_service", "access.py")
_spec = importlib.util.spec_from_file_location("rag_document_access", _ACCESS_PATH)
doc_access = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(doc_access)

MAX_CORPORA = 256


class ScopeError(ValueError):
    pass


class Scope:
    def __init__(self, corpus_ids, access=None):
        self.corpus_ids = frozenset(corpus_ids)
        self.access = access

    @classmethod
    def parse(cls, body: dict) -> "Scope":
        raw = body.get("corpus_ids")
        if not isinstance(raw, list) or len(raw) > MAX_CORPORA \
                or any(not isinstance(c, str) or not c for c in raw):
            raise ScopeError("corpus_ids must be a list of corpus ids (an empty list sees nothing)")
        try:
            access = doc_access.parse_access(body.get("access"))
        except ValueError as e:
            raise ScopeError(str(e))
        return cls(raw, access)

    def allows(self, doc) -> bool:
        return doc.corpus_id in self.corpus_ids and doc_access.doc_allowed(doc.acl, self.access)


def parse_doc_acl(raw):
    return doc_access.parse_doc_acl(raw)
