"""Multi-turn conversation session manager for the adapter.

The ViVeSecBox has no explicit session start/end signal; the AIBox keeps a
user's conversation in memory and spills it to disk after inactivity, reloading
it when the user returns (a requirement confirmed by the ViVeSecBox team).

A session is identified by the (VVS-User, VVS-Drive) pair. Because the ACL is
per-request and drive-scoped (the drive decides what the user may see), the
conversation is drive-scoped too: the same user on a different drive gets a
separate, isolated conversation -- this prevents one drive's context from
leaking into another drive's answers.

Pure stdlib, thread-safe. Disk layout: one JSON file per session under
spill_dir, named by a stable hash of the (user, drive) key, written atomically.
Memory is a write-through cache: every recorded exchange is persisted, and idle
sessions are evicted from memory (their disk copy stays) by a periodic sweep.

Since F1 (persistent threads) the store itself lives in conversations.py; the
session file above is that store's ``default`` thread.
"""
import hashlib
import re

from conversations import ConversationStore


def session_key(user, drive):
    """Stable, filesystem-safe identifier for a (user, drive) conversation."""
    raw = (user or "") + "\x00" + (drive or "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def followup_evidence(question, history, corpus_ids):
    followup = re.search(
        r"\b(?:korábban|előbb|előző|ellentmond\w*|mégis|visszavon\w*|azt mondtad|"
        r"azt írtad|ezt írtad|miért állít\w*|previous|earlier|you said|you wrote|contradict\w*)\b",
        question, re.I)
    short_reference = len(question.split()) <= 14 and re.search(
        r"\b(?:róla|őt|ő|erről|ebben|ezt|ennek|ugyanez|that|it|he|she|they)\b", question, re.I)
    if not followup and not short_reference:
        return []
    evidence = []
    for turn in reversed(history[-6:]):
        if turn.get("role") != "assistant":
            continue
        for source in turn.get("sources", []):
            chunk_id = source.get("chunk_id")
            if source.get("corpus_id") in corpus_ids and chunk_id and chunk_id not in evidence:
                evidence.append(chunk_id)
    return evidence[:8]


class SessionManager(ConversationStore):
    """Compatibility surface over the thread-aware ConversationStore.

    history(user, drive) / record(user, drive, ...) without a conversation_id
    address the ``default`` thread, which is the legacy per-scope session file
    -- so every pre-thread caller keeps its behaviour. max_turns counts
    EXCHANGES (a user question + its assistant answer) handed to the model.
    """

    def __init__(self, spill_dir=None, idle_seconds=900, max_turns=12, **kwargs):
        super().__init__(spill_dir=spill_dir, idle_seconds=idle_seconds,
                         max_turns=max_turns, **kwargs)

    @classmethod
    def from_env(cls, env=None):
        return super().from_env(env)
