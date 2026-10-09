# ViVeSec AIBox adapter

Translates the **ViVeSec v2** external protocol (what the ViVeSecBox / simulator
speaks) into the **RAG service contract** (`drive_sync_api_spec.txt`). This is the
AIBox's external face: the only thing the ViVeSecBox talks to. The RAG service
sits behind it on loopback and stays contract-pure (drop-in swappable).

```
ViVeSecBox ──ViVeSec v2──▶ adapter ──RAG contract──▶ rag_service ──▶ Ollama bge-m3
 (VVS-Drive/User)          (this)     (corpus_id/tenant)
```

## What it does

| ViVeSec v2 front (exposed)                     | RAG contract back (called)              |
| ---------------------------------------------- | --------------------------------------- |
| `GET  /api/v1/status`                          | `GET /stats` (+ watchdog, mirror stats) |
| `GET  /api/v1/version`                         | `GET /health` (release identity only)   |
| `POST /api/v1/index/get`                       | *(served from the local mirror)*        |
| `POST /api/v1/index/get/children`              | *(served from the local mirror)*        |
| `POST /api/v1/index/upsert/directory`          | `POST /index/upsert/directory`          |
| `POST /api/v1/index/upsert/file/check`         | `POST /index/upsert/file/check`         |
| `POST /api/v1/index/upsert/file/content/{tok}` | `POST /index/upsert/file/content/{tok}` |
| `POST /api/v1/index/drop/tree`                 | `POST /index/drop/tree`                 |
| `POST /api/v1/ui/query`                        | `POST /rag/search_context`              |
| `POST /api/v1/storage/unlock`                  | *(stub until LUKS/init wired)*          |

Key translation decisions:

- **corpus_id** is derived from the drive root (`corpus.py`): readable slug +
  `sha1(drive_root)[:8]`. The hash makes it collision-free, so a drive named
  `beta dev 2` and one named `beta2`/`beta-dev-2` never collapse to one corpus.
- **tenant_id** is pinned to a constant (one physical box = one customer).
- **VVS-Drive** is the mandatory hard filter — implemented as corpus isolation
  (one drive = one corpus). Re-read per request, never cached (ACL safety).
- **VVS-User** is audit/log only; it never affects routing.
- The RAG contract has **no listing endpoint**, so the adapter keeps its own
  metadata mirror (`meta.py`, `path -> {file, mtime, size}`) to answer the
  diff-sync reads (`/index/get`, `/index/get/children`). Content is never
  mirrored — only metadata.

## Run

```powershell
$env:ADAPTER_PORT="8088"; $env:RAG_URL="http://127.0.0.1:8090"; python adapter/service.py
```

| Env                          | Default                 | Meaning                          |
| ---------------------------- | ----------------------- | -------------------------------- |
| `ADAPTER_HOST`/`ADAPTER_PORT`| `127.0.0.1` / `8080`    | bind address                     |
| `RAG_URL`                    | `http://127.0.0.1:8090` | RAG service base URL             |
| `RAG_API_KEY`                | *(empty)*               | sent as `X-API-Key` to the RAG   |
| `ADAPTER_TENANT_ID`          | `default`               | pinned tenant                    |
| `ADAPTER_DRIVE_PREFIX`       | `/storage/drives`       | virtual drive root prefix        |
| `ADAPTER_META_PATH`          | *(in-memory)*           | mirror JSON path (persist)       |
| `ADAPTER_WATCHDOG_SECONDS`   | `90`                    | status watchdog window           |
| `ADAPTER_MAX_CONTEXT_TOKENS` | `4000`                  | query context budget             |

This table lists the most common settings only. Every parameter, with its
default, allowed range, fail-safe behaviour and impact, is in the configuration
register: [`CONFIGURATION.md`](../CONFIGURATION.md), generated from
`scripts/release/config_registry.json`. A new environment variable must be added
there; `scripts/release/release_test.py` fails otherwise.

## Release identity

The adapter reports the release it was built from (E01). The build stamps
`adapter/build_info.json` (git-ignored) with the calendar version from the
repository `VERSION` file (`YY.MM.N`) and the git commit; see
[`scripts/release/README.md`](../scripts/release/README.md).

- `/api/v1/status` carries `version` (`release`, `version`, `commit`,
  `consistent`, `ui_version`, `components.adapter`, `components.rag`) and
  `paired`. `consistent` is false when the RAG runs a different build or is
  unreachable, which is how a partial redeploy shows.
- `GET|POST /api/v1/version` returns the same `version` block plus `paired`,
  `ws_fs.connected` and `storage_locked`. Unlike `/status` it is **not** a
  ViVeSecBox presence poll, so monitoring (the fleet overview, the box manifest)
  never keeps the presence watchdog from locking the storage.
- An unstamped dev checkout reports `<VERSION>-dev`; a missing or malformed
  stamp reports `unknown` and never stops the service.
- `ADAPTER_UI_VERSION` / `GET /api/v1/ui-version` is unrelated: it tells the
  ViVeSecBox which embedded UI bundle path (`latest` or a version) to load.

## Customer Chat Profiles

One adapter/UI build supports four deployment policies. Set
`ADAPTER_CHAT_POLICY` on the adapter container, not on the UI or RAG service:

| Value | Default chat profile | User can switch |
| --- | --- | --- |
| `locked_hybrid` (default) | Hybrid | No |
| `locked_grounded` | Grounded | No |
| `selectable_grounded` | Grounded | Yes |
| `selectable_hybrid` | Hybrid | Yes |

An absent policy defaults to `locked_hybrid`; an unknown value fails closed to
`locked_grounded`. The profile selector is hidden for both locked policies.
One AI Box is
one customer deployment; this is not a multi-tenant administration API. No new
customer-admin role is required. End users can select a profile for their own
chat when enabled, but cannot change the deployment policy or another user's
profile. The choice currently lasts for the mounted UI session; reload uses
the deployment default. Each profile's server-side history remains persistent
with the existing session settings. The UI keeps both transcripts while open
and shows the selected one; it does not restore transcripts after reload.

`POST /api/v1/status` advertises `chat_policy` with `policy`, `default_profile`
and `allow_switch`. `/api/v1/ui/query` and `/api/v1/ui/ask` accept optional
`profile: "grounded" | "hybrid"`. Omission uses the deployment default;
invalid values return 400; plain-chat requests for a profile other than the
locked deployment default return 403. Explicit or parsed quick actions always resolve to grounded,
including slash actions launched from the chat UI. The existing `mode` field
still means search `files`/`text`, not chat profile.

Grounded retains the previous generation and confidence rules. Hybrid answers
general questions from general knowledge by default, without requiring the user
to opt out of document grounding. Unrelated documents and earlier refusals must
not turn a general question into a document-only request. It supports
general explanations, drafting and suggestions, but instructs the model to
ground company facts in the retrieved documents and distinguish assumptions
and suggestions. Both profiles use the same authorized retrieval scope, local
model and context size. Hybrid is not an autonomous agent and adds no web or
cloud access. Retrieval failures remain failures, not silent knowledge fallbacks.
General requests still run retrieval unless the existing conversation rewriter
identifies a conversation-only turn. Semantic company-fact enforcement is
prompt-based, not a guarantee against hallucination; evaluate on customer data.

Hybrid responses have `confidence: null`, a top-level `audit_id`, and `profile`;
the displayed/exported text includes a hybrid audit footer. Only cited current
document references are returned as citations; unknown reference markers are
removed. Feedback traces and durable jobs retain the profile. Grounded
responses keep their confidence score and also expose profile/audit metadata.

### Retrieval And Source Continuity

The SQLite backend combines corpus-filtered vector candidates with FTS5/BM25
text candidates using weighted reciprocal-rank fusion. The query embeddings
are shared across authorized drives. Each branch retains at most
`min(max(top_k * 8, 40), 256)` candidates (vector searches run per corpus);
the returned context is capped at 50 chunks and uses the existing token budget.
Identical normalized text within one corpus is collapsed; copies on different
authorized drives retain their own provenance. This is exact-text deduplication,
not a guarantee that near-identical document versions are all recognized.
An explicitly configured `RAG_MIN_SCORE` still applies to every result.

The adapter sends selected or explicitly named files as `source_paths` to
`/rag/search_context`, before ranking and top-k selection. Full paths are matched
as paths; bare filenames may match several files across the authorized scope.
Missing files return no context, never unrelated fallback documents. Quote
filenames containing spaces. The JSON backend implements the same constraints,
but remains an in-memory reference implementation, not the large-corpus backend.

Completed answers persist only their actually cited chunk IDs and provenance
alongside session text. A bounded Hungarian/English follow-up heuristic can send
up to eight IDs as `evidence_chunk_ids`; retrieval rechecks the current corpus and
file filters and reloads current chunk text. Up to three surviving IDs receive
priority. Conversation text itself is not copied into retrieval evidence.
New-topic questions normally omit these IDs. Old sessions without provenance
remain readable but cannot recover earlier citations through this mechanism.
Re-ingested chunk IDs refer to the current index, not an immutable old snapshot.
The response/job includes `retrieval_debug`, `source_paths` and
`evidence_chunk_ids` for diagnosis.

SQLite startup builds an FTS5 index from stored chunk text once; it does not
re-embed documents. Triggers maintain it on insertion, replacement and deletion.
The completion marker is committed with the backfill, allowing interrupted
backfills to retry. Before rollout, back up the SQLite index with a consistent
SQLite backup and test startup, disk growth and representative queries on a copy.
Deploy the RAG service before the adapter so the strict source constraints are
supported. Keep the old image and pre-migration backup for rollback. Large-corpus
latency and real-model answer quality require measurements on customer data;
unit and smoke tests alone do not establish either.

Hybrid history has a separate namespace for the same user and authorized scope.
Existing grounded session keys are unchanged. Quick actions only see grounded
history, never hybrid proposals. User text copied into a quick-action brief
remains untrusted task input, not verified evidence. Async jobs snapshot both
the effective profile and its history at submission. Switching the UI does not
change submitted jobs; polling an existing hybrid job still returns its original
hybrid result even after the policy is locked.

### Rollout and Rollback

1. Keep the previous adapter image and both LAN/SSR and embed UI artifacts.
2. Deploy the new adapter and UI with `ADAPTER_CHAT_POLICY=locked_hybrid`
  for the current fixed-hybrid rollout. To preserve the old behavior instead,
  explicitly set `locked_grounded`; new UI against an old adapter also stays
  locked grounded.
3. Change the policy by recreating the adapter container with the
  desired environment value, preserving all existing environment and mounts.
  Docker restart alone does not change container environment variables.
4. Reload the UI to read the new capability/default. An older UI cannot display
  or choose profiles: do not enable hybrid before the UI is upgraded. The
  embedded UI is a separate artifact and must also be updated.
5. To revert behavior, restore `ADAPTER_CHAT_POLICY=locked_grounded`, recreate
  the adapter and reload the UI. No model swap, reindex or data migration is
  needed. Existing hybrid results remain labelled hybrid, not reclassified.

Changing adapter containers interrupts running requests and the box filesystem
channel; schedule policy changes outside active sync/generation. Stored results
survive if the existing job/session mounts are retained. A full binary rollback
also uses the previous UI artifacts; hybrid histories are left untouched but
are not read by the legacy grounded session keys. Review/export hybrid results
before a binary rollback if continued old-UI access to them is required.

Focused tests (from the adapter directory):
`python -m unittest chat_policy_test llm_stream_test`, and for the release
identity `python -m unittest version_http_test`.
The HTTP tests use isolated temporary stores and mocked RAG/model responses;
they prove routing, ACL boundaries, history separation, audit and job behavior,
not real-model factual accuracy. Real-model acceptance must cover all supported
languages, missing company data, mixed facts/advice, prompt injection, and
quick-action regression before customer rollout.

## Test

```powershell
python adapter/smoke_test.py        # boots RAG + adapter, 20 checks
python sim/vivesecbox_sim.py sync   # drive the adapter with the real simulator
```
