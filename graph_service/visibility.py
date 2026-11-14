"""Who may see what: the caller's scope applied to graph documents (E26).

The adapter decides the scope per request (allowed corpora + flattened access
filter) and sends it with every query. The graph never stores its own copy of
the rights; it reuses the document rule of rag_service/access.py so both
services decide identically.
"""
from __future__ import annotations

import importlib.util
import os

from domain_profile import protected_member, protected_relations

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
    def __init__(self, corpus_ids, access=None, profile=None):
        self.corpus_ids = frozenset(corpus_ids)
        self.access = access
        # E29: protected data (an informant's identity) only for a member of the
        # profile's protected_principals; a non-member does not even get the
        # protected relation types, whatever document would back them.
        self.protected_member = protected_member(profile, access)
        self.hidden_relations = frozenset() if self.protected_member \
            else protected_relations(profile)

    @classmethod
    def parse(cls, body: dict, profile: dict | None = None) -> "Scope":
        raw = body.get("corpus_ids")
        if not isinstance(raw, list) or len(raw) > MAX_CORPORA \
                or any(not isinstance(c, str) or not c for c in raw):
            raise ScopeError("corpus_ids must be a list of corpus ids (an empty list sees nothing)")
        try:
            access = doc_access.parse_access(body.get("access"))
        except ValueError as e:
            raise ScopeError(str(e))
        return cls(raw, access, profile)

    def allows(self, doc) -> bool:
        # A structured record also carries its own rule: both must admit the caller.
        if doc.corpus_id not in self.corpus_ids or not doc_access.doc_allowed(doc.acl,
                                                                              self.access):
            return False
        rule = getattr(doc, "record_acl", None)
        if rule is None:
            return True
        if rule.get("protected"):
            # Membership by the profile now (not the principals stored at import);
            # the stored rule still counts for its classification and denies.
            return self.protected_member and doc_access.doc_allowed(
                dict(rule, restricted=False), self.access)
        return doc_access.doc_allowed(rule, self.access)


def parse_doc_acl(raw):
    return doc_access.parse_doc_acl(raw)
