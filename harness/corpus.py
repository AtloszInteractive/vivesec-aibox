"""Fixture corpus loader + ingest for the harness (v1 retrieval contract).

Reads a fixtures `manifest.json` and ingests each document into the v1 engine
via POST /ingest. Two isolation profiles are supported:

  * SMOKE (legacy, `fixtures/`): one FILE = one corpus. Tiny hand-written set.
  * FULL  (generated, `fixtures_full/`): one DRIVE = one corpus with MANY files
    (manifest v2, each document carries a `drive` field) — this matches the
    production ACL semantics (a user sees a whole drive or nothing), and gives
    real distractor density inside a corpus.

Either way the corpus boundary IS the ACL pre-filter: a gold case's
`allowed_file_ids` selects WHICH corpora the query may touch, and hits are
resolved back to file_ids via their `source_path` (see `build_path_map`), so a
hit that cannot be attributed to an allowed file is a leak by construction.
"""
from __future__ import annotations

import json
import os

from client import EngineClient

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(HERE, "fixtures")
TENANT = "harness"


def corpus_id_for(file_id: str) -> str:
    """Stable corpus_id for a legacy (one-file) fixture file_id.

    e.g. 'vivesec://files/finance-q4' -> 'harness-finance-q4'
    """
    slug = file_id.rstrip("/").rsplit("/", 1)[-1] or file_id
    return f"harness-{slug}"


def corpus_id_of(doc: dict) -> str:
    """corpus_id of a manifest document: drive-scoped when the doc has a
    `drive` (manifest v2 / FULL profile), else the legacy per-file corpus."""
    drive = doc.get("drive")
    return f"harness-drive-{drive}" if drive else corpus_id_for(doc["file_id"])


def load_manifest(fixtures_dir: str = FIXTURES_DIR) -> list[dict]:
    with open(os.path.join(fixtures_dir, "manifest.json"), "r", encoding="utf-8") as f:
        return json.load(f)["documents"]


def build_corpus_map(fixtures_dir: str = FIXTURES_DIR) -> dict[str, str]:
    """Deterministic {corpus_id -> file_id} map (LEGACY one-file corpora only).

    In the FULL profile a corpus holds many files, so per-corpus attribution is
    ambiguous — use `build_path_map` for hit resolution instead. Entries here
    are still emitted for drive corpora (last file wins) purely as a fallback.
    """
    return {corpus_id_of(d): d["file_id"] for d in load_manifest(fixtures_dir)}


def build_file_to_corpus(fixtures_dir: str = FIXTURES_DIR) -> dict[str, str]:
    """{file_id -> corpus_id} — which corpus must be queried for a gold file."""
    return {d["file_id"]: corpus_id_of(d) for d in load_manifest(fixtures_dir)}


def _norm(path: str) -> str:
    if path and len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path


def build_path_map(fixtures_dir: str = FIXTURES_DIR) -> dict[str, str]:
    """{normalized source_path -> file_id} for hit attribution.

    The engine's contexts carry `source_path` (what we ingested as `path`), so
    this resolves every hit to its gold file_id — also inside multi-file drive
    corpora, where the corpus alone cannot identify the file.
    """
    return {_norm(d["path"]): d["file_id"] for d in load_manifest(fixtures_dir)}


def ingest_corpus(client: EngineClient, fixtures_dir: str = FIXTURES_DIR) -> dict[str, str]:
    """Ingest every fixture into its corpus (drive corpora hold many files).

    Each corpus is cleared ONCE up front so repeated runs are deterministic.
    Returns the legacy {corpus_id -> file_id} map (see build_corpus_map).
    """
    docs = load_manifest(fixtures_dir)
    corpus_to_file: dict[str, str] = {}
    cleared: set[str] = set()
    for d in docs:
        file_id = d["file_id"]
        corpus_id = corpus_id_of(d)
        with open(os.path.join(fixtures_dir, d["path"]), "r", encoding="utf-8") as f:
            text = f.read()
        if corpus_id not in cleared:
            client.drop_tree(corpus_id, "/", keep_exact=False)
            cleared.add(corpus_id)
        client.ingest(
            {
                "corpus_id": corpus_id,
                "tenant_id": TENANT,
                # v1 spec field is `source_path`; keep `path` too so both our
                # rag_service (accepts either) and spec-strict engines work.
                "path": d["path"],
                "source_path": d["path"],
                "title": d.get("title", ""),
                "text": text,
                "metadata": {
                    "file_id": file_id,
                    "acl_scope": d.get("acl_scope", []),
                    "sensitivity": d.get("sensitivity"),
                    "doc_type": d.get("doc_type"),
                    "language_hint": d.get("language_hint", "en"),
                    "mtime_days_ago": d.get("mtime_days_ago"),
                },
            }
        )
        corpus_to_file[corpus_id] = file_id
    return corpus_to_file
