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
| `POST /api/v1/index/upsert/acl`                | `POST /index/acl/update` (E03)          |
| `POST /api/v1/index/drop/tree`                 | `POST /index/drop/tree`                 |
| `POST /api/v1/ui/query`                        | `POST /rag/search_context`              |
|   *(analyst full reading, `read_all`)*         | `POST /rag/match_chunks`                |
| `POST /api/v1/ui/files/children`               | *(mirror, scope + ACL; E03)*            |
| `GET  /api/v1/ui/insight`                      | `POST /rag/scope_stats` (E03)           |
| `POST /api/v1/ui/graph/*`                      | graph_service `/query/*` ... (E26)      |
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

## Identity, directory and lifecycle (E02)

The ViVeSecBox owns users and login; the adapter verifies what it is told and
fails closed. Every switch below defaults to today's behaviour, so a box keeps
working with a ViVeSecBox that does not send signed assertions yet. Each
parameter is listed in [`CONFIGURATION.md`](../CONFIGURATION.md).

**Signed identity assertion** (`identity.py`). `ADAPTER_IDENTITY_MODE`:

| Value | Behaviour |
| --- | --- |
| `off` (default) | `VVS-User` is used as sent; tokens are ignored |
| `verify` | a token in `VVS-Identity` must be valid; without one the header identity is used |
| `require` | every user endpoint needs a valid token, otherwise `401` |
| anything else | treated as `require` |

The token is a compact JWT signed with HS256 (secret from
`ADAPTER_IDENTITY_HS256_SECRET_FILE`, at least 32 bytes) or RS256 (public keys
from `ADAPTER_IDENTITY_JWKS_FILE`, RSA of 2048 bits or more, reloaded when the
file changes), restricted by `ADAPTER_IDENTITY_ALGORITHMS`; `alg=none` is always
rejected. `exp` is mandatory and the lifetime is capped by
`ADAPTER_IDENTITY_MAX_TTL_SECONDS`; `iss`, `aud` and `scope`/`scp` are checked
when configured. Binding: the user claim (`sub` by default) must equal
`VVS-User`, a `drive` claim must equal `VVS-Drive`, a `sid` claim must equal
`VVS-Session`. A `drives` claim replaces the unsigned `VVS-Other-Drives` header
as the source of the extra drives. `upn`/`preferred_username`/`email`,
`groups` and `roles` are read when present. A rejected request gets a generic
`identity not accepted`; the reason is logged.

**Groups and roles** (`directory.py`). `ADAPTER_DIRECTORY=off|token|entra`
(unknown means `off`). `token` uses the claims of a verified token; `entra`
reads transitive group membership and the app role assignments of
`ADAPTER_ENTRA_RESOURCE_ID` from Microsoft Graph with client credentials
(`ADAPTER_ENTRA_TENANT_ID`, `ADAPTER_ENTRA_CLIENT_ID`,
`ADAPTER_ENTRA_CLIENT_SECRET_FILE`). Admin-consented application permissions:
`User.Read.All` + `GroupMember.Read.All` for groups with their names, plus
`Directory.Read.All` when roles are resolved (`ADAPTER_ENTRA_RESOURCE_ID` set);
`Directory.Read.All` alone covers both. The UPN (or an e-mail-shaped user
id) is the lookup key. Results are cached for `ADAPTER_DIRECTORY_TTL_SECONDS`;
when the directory is unreachable and the entry has expired, the user has **no**
groups. `GET /api/v1/ui/init` returns `identity` (`source`, `upn`, `groups`,
`roles`, `directory.error`, and since E03 `clearance` and `acl`); the groups and
roles drive the document ACL described below.

**Plain-HTTP scope.** `ADAPTER_HTTP_SCOPE=pairing` limits the plain-HTTP
listener, for network peers, to `/`, `/api/v1/status`, `/api/v1/version`,
`/api/v1/ui-version`, `/api/v1/init/prepare` and `/api/v1/init/commit`;
everything else answers `403` and must use the mTLS listener. Direct loopback
callers (on-box scripts) are not restricted. A request relayed by a local
reverse proxy (`Forwarded`, `X-Forwarded-For`, `X-Forwarded-Host` or
`X-Real-IP`; the box-hosted UI always sets `X-Forwarded-Host`) counts as a
network peer, so the LAN demo UI does not work in the pairing scope. Default
`full`; an unknown value means `pairing`. Only enable it on a box whose
ViVeSecBox has paired and uses mTLS (`ADAPTER_TLS=on`). The box-hosted UI
server relays only `/api/v1/ui/*`, `status`, `version` and `ui-version`; the
`index/get/children` sync read only for the demo drive picker
(`ADAPTER_DEMO_DRIVE_PICKER=1`) and only for the drive root.

**User lifecycle** (`lifecycle.py`). The ViVeSecBox reports directory events:

```http
POST /api/v1/identity/lifecycle      (mutual TLS or a direct, non-relayed
                                      loopback caller only, else 403)
{"event": "deleted|suspended|revoked|reinstated", "user": "<VVS-User id>",
 "upn": "<optional>", "reason": "<optional>", "event_id": "<optional>"}
```

A locked user (matched by user id or UPN) gets `403 user access locked` on
every user endpoint, so their threads, jobs and generated files are
unreachable; their queued and running jobs are cancelled. Nothing is deleted.
`reinstated` lifts `suspended`/`revoked`; `deleted` is final (`409`). The state
is persisted atomically in `ADAPTER_LIFECYCLE_PATH` (deploy scripts:
`/data/adapter/lifecycle.json`; empty keeps it in memory). An unreadable state
file refuses every user request with `503` until repaired. A factory reset
clears it.

## File, folder and room ACL (E03)

The drive scope decides which drives (corpora) a request may search; `acl.py`
decides which documents inside them the caller may see. It is the single
implementation of that rule: the RAG stores the flattened result per document
and filters **inside** its queries, and every adapter path that names a file
asks the same policy. Covered: retrieval (dense, keyword and follow-up
evidence), `#analyze`, `#search files`, file names in a question, the folder
listing (`POST /api/v1/ui/files/children`), the original-file download
(`/api/v1/ui/file`), the per-user system view (`GET /api/v1/ui/insight`), the
hit count of a search, background jobs, and the drive list of `/ui/scope` (a
room the ACL hides is not offered).

**Model.** Principals are `user:<id>`, `group:<id>`, `role:<value>` and
`everyone`, compared case-insensitively; the caller's principals are the
`VVS-User` id, the UPN and, from `ADAPTER_DIRECTORY`, the groups and roles. A
room (drive root), folder or file may carry

```json
{"allow": ["group:<id>", "role:<value>"], "deny": ["user:<id>"],
 "inherit": true, "classification": "confidential"}
```

- **Deny first**: a deny on any level of the path hides the document.
- **Allow** lists inherit downwards; the nearest level that defines one
  decides. `inherit: false` stops the parent allow list (with no allow list of
  its own, nobody sees the subtree); denies always inherit.
- No allow list anywhere on the path = everyone who may reach the drive sees it
  (the pre-E03 behaviour). Without any ACL data the box behaves as before.
- **Classification** `public < internal < confidential <
  strictly_confidential < personal` (Hungarian aliases accepted): the highest
  level on the path, `ADAPTER_ACL_DEFAULT_CLASSIFICATION` when none is set. A
  user sees documents up to their clearance (`ADAPTER_ACL_DEFAULT_CLEARANCE`,
  or the highest mapping that matches in the rules file).
- Fail-closed: a malformed ACL hides its subtree, an unknown classification is
  the highest level, an unusable rules file hides everything, and when the
  group membership is unknown (directory `off` or failing) a group/role allow
  never matches and any group/role deny hides the document.

**Sources.** (a) The ViVeSecBox sync, **proposed contract** until agreed: an
optional `acl` object on `POST /api/v1/index/upsert/directory` and
`.../file/check`, and `POST /api/v1/index/upsert/acl {path, acl}` for a
permission change without new content. A missing `acl` key keeps the stored
ACL, `"acl": null` clears it. The ACLs are kept in the mirror beside the
entries; `/index/get*` answer exactly as before. (b) The administrator rules
file `ADAPTER_ACL_RULES`:

```json
{"paths": {"/storage/drives/finance": {"deny": ["role:intern"]},
           "/storage/drives/finance/board": {"classification": "strictly_confidential"}},
 "clearance": {"default": "internal", "role:board": "strictly_confidential"},
 "default_classification": "internal"}
```

When both define the same node, allow lists intersect, denies add up and the
higher classification wins. The file is re-read when it changes.

**Keeping the RAG in line.** A sync call sends the flattened ACL with the
document; a changed folder or file ACL is pushed for the whole subtree
(`/index/acl/update`, metadata only, nothing is re-embedded). At start-up,
after a rules-file change and after a failed push the adapter resyncs every
document (`_acl_loop`, every 30 s until it succeeds). Meanwhile the adapter
re-checks every context the RAG returns and drops what the policy hides, so
a lagging copy cannot leak content. Counts are only exact once the resync is
done. A corpus with hidden chunks is ranked with a filtered exact scan instead
of the partitioned KNN, so its query cost grows with the corpus size.

**Status and admin.** `/api/v1/status` reports the corpus-wide `index` and
`mirror` counters only to the trusted channel (mTLS or a direct loopback
caller); the UI uses `/api/v1/ui/insight`. The effective permission of a user:

```http
POST /api/v1/admin/acl/effective     (mutual TLS or a direct, non-relayed
                                      loopback caller only, else 403)
{"user": "lars", "upn": "<optional>", "groups": ["<what-if>"], "roles": [],
 "paths": ["/storage/drives/finance/hr/a.pdf"], "under": "/storage/drives/finance"}
```

returns the principals, the clearance, whether the membership is known, and per
path the decision, its reason and the contributing rules; `under` counts the
visible and hidden files of a subtree. Without `groups`/`roles` the configured
directory is asked. The drive scope is decided per request and is not part of
it. An administration UI for this view belongs to E12.

`ADAPTER_ACL=off` disables the document ACL (diagnostics only; the drive scope
still applies); an unknown value means `enforce`.

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

## Relationship analysis (E26)

Off unless `GRAPH_URL` points at the internal `graph_service`. Then:

- **Sync**: after the RAG committed a synced file, its bytes go to a background
  forwarder (`graph.py`) that hands them to the graph's extraction queue; a
  delete (`/index/drop/tree`) and an ACL push follow in the same FIFO order. The
  document ACL is computed when the file is sent, so it is never older than a
  change made while it waited. A busy (429) or unreachable graph is retried; a
  full forward queue (`ADAPTER_GRAPH_QUEUE_MB`) or a file over
  `ADAPTER_GRAPH_MAX_FILE_MB` is dropped and counted (re-sync to fill the gap).
  Queued bytes stay in memory.
- **Queries**: `POST /api/v1/ui/graph/query` `{query, params, drives?}` with
  `query` one of find, entity, path, common, copresence, shared, timeline,
  neighborhood, communities, ranking. The adapter adds `corpus_ids` and
  `access` itself (the client's own values are overwritten). Every document an
  answer cites is re-checked against the current policy; a hidden one (the
  graph's ACL copy lags) fails the call with 503 and schedules a resync.
- **Chat**: a plain question (no quick action) that names entities the graph
  knows (`/query/mentioned`: names with Hungarian case endings, identifier
  patterns) gets the path between the first two and their direct relations as
  extra contexts (`graph_context.py`), each with its verbatim quote, document
  and page, ahead of the RAG excerpts; the response carries `graph`
  `{entities, facts}`. Facts whose documents the caller may not see are dropped.
  `ADAPTER_GRAPH_CHAT=off` disables it; a graph error never blocks the answer.
- **Progress**: `POST /api/v1/ui/graph/status` lists only files the caller may
  see; queue sizes and totals are box-wide numbers.
- **Derived data leaks** (concept 5.1, pilot step 4): the graph computes every
  answer on the caller's visible subgraph -- an edge, attribute, name variant or
  subtype only from documents they may read (a subtype is stored per document
  as a reserved `subtype` attribute since extractor revision 5; before, the
  first document's subtype was global and a hidden document could type a
  visible person). `graph_service/leakage_test.py` proves it differentially:
  for 730 requests over every query, analytics with and without namesake
  joining, `documents/profile` and the review list/history, a caller restricted
  by group ACL, classification, a deny rule and the drive scope gets byte for
  byte what a graph of only their documents answers (also after a merge), while
  a caller who may read everything gets different answers for every query
  kind. Neo4j variants (direct and cached) run with `GRAPH_TEST_NEO4J_URI`.
  Not covered by it: the identity of a person is global -- whether two visible
  mentions are one person may have been decided using a document the caller
  cannot read (resolution by a registry number in a hidden document).
- **Review and profile**: `/api/v1/ui/graph/review/list` (scoped; `q` filters
  on the two names, `reason` on the match reason, `offset`/`limit` page; the
  response adds `open_total` and the `reasons` counts), `/review/decide` (one
  `{a, b}` or up to 1000 `pairs`; a pair no longer open in scope -- e.g. folded
  by an earlier pair of the batch -- comes back under `skipped`),
  `/review/undo {a, b}` and `/profile/set` only for users in
  `ADAPTER_GRAPH_ADMINS` (empty: nobody); `/review/history` lists the decisions
  newest first (who, when, `undoable`) as far as the caller may see both people
  as they were before the decision, plus `can_undo`; `/profile/get` returns the
  profile, the templates and `can_edit`. A merge records what the folded person
  brought (mentions, attributes and relationship evidence per document, with the
  document paths); undoing it revives that person and writes those documents
  back onto it: a document only that person was named in moves back whole (also
  a resynced, newer version at the same path), a document both were named in
  moves back what the record lists, and a relationship between the two, which
  the merge removed, returns. A document that reached the merged person later
  through the folded person's details stays with it. Undoing either decision
  reopens the pair. Merges decided before this existed cannot be undone. The
  graph keeps every decision and undo, with the deciding user, in
  `GRAPH_REVIEW_PATH`. Tests: graph `review_undo_test`, adapter
  `graph_integration_test` (`test_review_in_bulk_with_history_and_undo`).
- **Reprocessing after a profile change** (`reprocess.py`): the graph records
  the profile and extractor revision each document was extracted with (`bv@3/e2`:
  a software update that changes extraction raises the revision, so its documents
  count as stale too; empty for documents from
  before this was recorded). `POST /api/v1/ui/graph/reprocess/status` gives the
  caller's view: current profile, counts per profile, `stale_total` (older or
  unknown), `partial_total` (of those: read without the language model, see
  below), `absent_total` (synced files of the caller's view the graph holds no
  document for -- a full forward queue during a large sync, the graph down, a
  failed extraction; the adapter's metadata mirror compared with the graph's
  paths, cached per user and scope while the graph's document count is
  unchanged, at most 30 s), `can_start`, the file `source` (`ws-fs` or `local`)
  and the current or last run. `/reprocess/start {path_prefix?}` (graph
  administrators; the folder must be inside the scope) re-reads, in this order,
  the absent files, the partial documents and the other stale ones the
  administrator may see (an absent file is sent with the RAG's doc_id,
  `corpus.doc_id_of`): each file is fetched again -- from the ViVeSecBox over ws-fs as that
  administrator, or from `ADAPTER_REPROCESS_SOURCE_DIR` on a development box --
  only when the forwarder and the graph queue are empty, and handed to the
  forwarder like a synced file (its current ACL applies). A file gone from the
  drive counts as missing, a refused one as failed; a lost ws-fs channel stops
  the run. `/reprocess/cancel` stops after the current file. A re-extracted
  document keeps its people's ids (the graph resolves the names it named before
  to the same, merge-followed ids first, unless the document now conflicts), so
  merge and keep-apart decisions survive. The UI shows this under
  Relationships › Processing. Tests: `reprocess_test`, `graph_integration_test`
  (`test_a_profile_change_is_reprocessed_from_the_file_source`,
  `test_synced_files_the_graph_never_got_are_caught_up`), graph side
  `ingest_jobs_test`.
- **Incremental extraction** (graph side): a file sent again with the same
  content (SHA-256, kept per document) and already extracted with the current
  profile is not extracted again -- a drive re-sync costs no model time; an ACL
  sent with it is applied. While more than `GRAPH_DEFER_PROSE_QUEUE` documents
  (default 50) wait, the next ones get rules, tables and forms only, with the
  profile tag `<tag>/partial`: the queue drains in seconds per file instead of
  holding raw files in memory for hours, and those documents count as stale
  until a reprocessing run fetches them again for the running text. A document
  whose model call failed is tagged partial too. Starting such a run is manual
  (graph administrator); running it unattended needs an identity allowed to
  fetch files from the ViVeSecBox -- a customer decision.
- **Places inside places** (profile `entity_subtypes[].within`, in `bv` from
  version 3: a cell is within an institution): such a place's identity includes
  its container, so cell 102 of two prisons are two places. The container is the
  one a `PART_OF` relation in the document names, else the only one the document
  names, else the one named last before the place on its page (within 300
  characters). The graph then adds `PART_OF` with the text between the two as
  the verbatim quote, keeps the container as the place's `within` attribute and
  labels the node "102. számú zárka (Kőhegyi …)" -- from documents the caller may
  see only. A place whose document names no container stays that document's own
  (no cross-document link), the safer choice: a placement list without the
  institution cannot tell whose cell 102 it lists. Likewise a place,
  organization or object named only by a common noun (no capital letter, no
  digit: "intézet", "gépjármű") stays its document's own. (German capitalises
  every noun, so there this rule does not catch common nouns.) Tests: graph
  `place_scope_test`.
- **Dossier**: `POST /api/v1/ui/graph/dossier` `{id, format: docx|pdf|md, lang,
  drives?}` renders one entity's basic data, direct relations, timeline and a
  numbered source list (path, page, verbatim quote) with `docgen.py`.
  Evidence from documents the caller may not see is left out, and so is a
  relation left without evidence. Response: `{name, format, content_type, size,
  text, content_b64}` (base64, because the ViVeSecBox tunnel carries JSON
  only); `text` is the markdown, which the UI can save with `/ui/save`.
- **GPU priority**: with `ADAPTER_GPU_BROKER_PORT` set, a loopback-only broker
  (`gpu_gate.py`) forwards `/api/chat`, `/api/generate` and
  `/v1/chat/completions` to Ollama one background call at a time, only while no
  adapter model call runs (plus `ADAPTER_GPU_GRACE_SECONDS`), and at the latest
  after `ADAPTER_GPU_MAX_WAIT_SECONDS`. Set the graph's `GRAPH_LLM_URL` to it. A
  background call already running is not interrupted.

Tests: `python -m unittest graph_integration_test` (real RAG and graph service,
fake extraction model; proves plumbing and filtering, not extraction quality).

### Analyst mode (`ADAPTER_APP_MODE=analyst`)

The box offers chat + relationships only. Quick actions (`action` in the body)
and `/ui/files/children` answer 403, a typed `/summary` is plain text, and
`/api/v1/status` reports `app {mode, drive_browser, quick_actions,
orchestrator}` so the UI hides the drive browser, the results column and the
slash menu. Source viewing (`/ui/file`) and saving (`/ui/save`) stay.

With `GRAPH_URL` set, every chat message goes through the orchestrator
(`orchestrator.py`): one JSON planning call chooses graph tools (relations,
connection, common, timeline, copresence, shared_identifiers, most_connected,
and the general ones: `find_pattern` = chains of up to 3 typed links,
`count` = links counted and ranked by entity / linked entity / month / year /
relation / linked kind, `sequence` = one relation followed by another within N
days), a standalone document search, or a question back to the user. The
planner gets the active domain profile's kinds and relations (cached 60 s), so
the same tools serve any customer profile. With `"more": true` the planner sees
a summary of the results (rankings with their top lines) and may add calls, at
most 3 rounds; a call already run is not repeated, and a round of repeats ends
the planning. The adapter runs
the tools itself in the caller's scope; the model only names entities, kinds and
relations, a kind word passed as a name ("Fogvatartott") filters by that kind,
and an unknown kind or relation skips that query with a note (never
a wider search). Counts come from the graph, never from the model. Either end
of a link may be the counted or pattern-starting side.
Namesakes (separate records with the same name) are used together with a note
when named directly; a partial name that fits different entities is asked back
with the options. Patterns, sequences and per-entity counts follow node ids, so
records awaiting an administrator's same-name merge (graph review) are not
joined; when such a query comes back empty or ranks entities, the answer notes
how many pairs await a decision in the caller's visible documents (graph notes
reach the answer model as SEARCH NOTES; for an empty pattern or sequence the
adapter also shows the reason under the answer, since the model may drop it).
`ADAPTER_GRAPH_NAME_JOIN=same_name` (default `off`; any other value is off)
lets patterns, sequences and counts treat the visible person records of one
name as one person for that query (`graph_service/namesakes.py`; the graph is
not changed). Records are never joined when their kinds differ (an inmate and a
visitor), when visible birth dates, mothers' names or ID numbers differ, or
when an administrator decided the pair "separate". The answer then lists the
people joined by name alone, under the answer as well, and an empty result
mentions how many names were left apart because their records conflict. This
is an identity rule for the customer to confirm; a strong key in the source
documents (e.g. an inmate registry number) is the better fix where available.
Each fact becomes a cited context with its quote (at most
`ADAPTER_ORCHESTRATOR_MAX_FACTS`); hidden evidence is dropped. A question back
is stored as the assistant's turn and answered with `clarify: true`. A failed or
unusable plan falls back to the plain path (condense + graph facts).
Tests: `orchestrator_test`, `graph_integration_test`; graph side:
`graph_service/analytics_test.py`, `graph_service/namesakes_test.py`.

#### Full reading (`read_all`)

When a message needs every case or mention in the documents themselves ("list
every report that mentions X", "everything said about Y"), the planner may add
`read_all {terms, mode}` (first round only; `ADAPTER_DEEP_READ=off` removes it,
default on). Instead of the top-k search, `deep.py` then reads EVERY passage of
the caller's scope that contains the terms: the RAG lists them with
`/rag/match_chunks` (scope and ACL in the query, reading order, pages of 500;
a term word of 4+ letters matches as a prefix, so inflected forms are found;
the words of a multi-word term must lie close together). The passages are read
in groups of `ADAPTER_DEEP_BATCH_CHARS` characters (default 9000); each group
is one JSON model call that returns findings with a verbatim quote, and a
finding whose quote does not occur in its passage is dropped. A reply cut off
at the output limit (4096 tokens; a register passage can hold dozens of facts)
keeps the findings finished before the cut and the group is read again in two
halves (at most 3 levels), so no passage goes unread; a single passage still cut
off alone is named in the note and makes the result partial. The findings
become cited contexts for the usual answer step, with a coverage note ("N
passages in M documents were read"). At most `ADAPTER_DEEP_MAX_CHUNKS` passages
are read (default 400); when more match, the note says the result is partial
and asks for a narrower question. Graph tools of the same plan still run.

Findings beyond `ADAPTER_DEEP_MAX_FINDINGS` (default 120) or
`ADAPTER_DEEP_ANSWER_CHARS` characters (default 60000) are condensed, not cut:
first one context per document (so every citation still names one document),
the documents over their fair share of the budget summarised by the model in
batched JSON calls (phase `condense`); only if that still does not fit are the
documents merged into digests across documents, which name their documents in
the text (at most 3 rounds). The note says how it was condensed; a summary the
model fails to write keeps the findings cut to the share and marks the result
partial, and only what still does not fit after the rounds is dropped (also
partial).

The reading holds the heavy scheduler slot (`HeavyScheduler.slot`), so its
model calls queue with the other heavy jobs; cancelling the job stops it before
the next group. The job reports `progress_phase` (`queued`, `collect`, `read`,
`condense`, `write`) with `progress_done`/`progress_total` in `/ui/poll` and
`/ui/jobs`, and the UI shows them in the waiting bubble and the job list. The
response carries `deep {terms, matched, read, documents, groups, findings,
condensed, partial, seconds}`.
Tests: `deep_test`, `scheduler_test`, `graph_integration_test`; RAG side:
`rag_service/match_chunks_test.py`.

#### Recognised text (E10b OCR)

The RAG reads scanned PDF pages and image files with Tesseract in the background
(`rag_service/ocr.py`; `RAG_OCR*` in `CONFIGURATION.md`). A passage from such a
page carries `ocr {quality, lang}` from the RAG; the adapter tags it in the
prompt (`[#3] scan.pdf (p.2) [OCR 87%]`) so the model knows it may hold
recognition errors, returns `ocr_quality` on the citation, and the footer lists
the source as `scan.pdf, p.2 (OCR 87%)`; the UI shows an OCR badge on the source
card. A full reading names the visible documents still waiting for OCR apart
from those without readable text (`scope_stats.ocr_pending`). Skipped pages and
documents waiting, interrupted or deferred are listed by the RAG's
`/index/skipped`; documents indexed before OCR become readable after a resync
from the ViVeSecBox (the box keeps no raw file).

`POST /api/v1/ui/index/skipped` gives a user the same list for their own scope:
the RAG filters by the scope's corpora and the caller's ACL in its query, and
the adapter checks every path again (scope + `_visible`), as it does for graph
answers. The answer has `count`, `reasons` (counts per reason), `pages_skipped`
and up to 500 `documents` with `reason`, `ocr_state` and `skipped_pages`; the
UI shows it under Relationships › Processing ("Not readable for search").

#### Purpose-bound queries and the query log (concept 5.2-5.3)

With `ADAPTER_PURPOSE=required` every content query -- a question (`/ui/query`,
`/ui/ask`, including quick actions and the file-name search), a graph query, the
review list and a dossier export -- must carry `case_ref` (ügyszám) and/or
`legal_basis` (jogalap) in its body; without one it gets 428
`purpose_required` (400 `purpose_invalid` for a value outside
`ADAPTER_LEGAL_BASES` or not matching `ADAPTER_CASE_REF_PATTERN`). Status and
processing views need none. `/status` reports `app.purpose {mode, legal_bases,
case_ref_pattern}`; the UI asks for the purpose once per browser session (a
chip in the chat header, a dialog) and sends it with every such call.

With `optional` or `required` each of those queries is written to the query log
(`querylog.py`): an append-only daily JSONL under `ADAPTER_QUERY_LOG_DIR` with
user, drive and scope, kind (`ask`, a quick action, `graph:<query>`,
`graph:review`, `export:dossier`), the purpose, the question's SHA-256 (its text
only with `ADAPTER_QUERY_LOG_TEXT=on`) and the result (audit id, citation
count, cited document paths, graph tools, full reading). Every record carries
the previous record's hash, so `querylog.verify(path)` finds an edited, inserted
or removed line; signing and anchoring the log is E06. When the purpose is
required and the log cannot be written, the query is refused (503) or its
answer withheld -- an unlogged answer is never returned. A job logs its outcome
when it finishes. Tests: `purpose_test`, `graph_integration_test`
(`test_graph_queries_and_exports_carry_their_purpose`).

#### Whole-document analysis of large files (E10a)

`analyze` (one named file) fetches every passage of the file
(`ADAPTER_ANALYZE_DOC_MAX_TOKENS`, default 3000000, bounds the fetch; beyond it
the result is marked partial). A file that fits `ADAPTER_ANALYZE_MAX_CHARS`
keeps the single-call path. A larger one is no longer cut off: `deep.read_document`
reads all passages in reading order in parts of `ADAPTER_ANALYZE_SECTION_CHARS`
characters (default 24000); each part yields up to 8 findings with verified
quotes and a page label (a part summary), a malformed reply is retried once and
then counted as failed (partial), and part summaries that do not fit are merged
into digests that keep the document as source. The final analysis runs on the
part summaries; the coverage note ("all N passages of X were read in K parts",
plus any partial reason) is shown under the answer and the response carries
`deep {kind: "document", read, total, groups, failed, parts, condensed, partial,
seconds}`. All calls use `ADAPTER_ANALYZE_NUM_CTX`, so the model is not reloaded
between parts. The job reports `read`/`condense`/`write` progress like a full
reading. `ADAPTER_ANALYZE_FULL=off` restores the old cut at the window (any other
value keeps full coverage). Not yet: resuming an interrupted analysis from its
last part. Tests: `analyze_full_test`, `deep_test`.

On an analyst box (where quick actions are off) the analysis starts two ways:
the planner's `analyze_document {file}` tool (first round, offered with
`read_all`; the named file is resolved among the files the caller may see, and
the analysis takes the heavy slot inside the plain ask), or the "Full analysis"
button next to a cited document, which sends `action: "analyze"` with that file
-- the one action an analyst box accepts (any other is still 403). The button's
analysis goes to the default thread like a quick action. Tests:
`graph_integration_test` (`test_analyst_analyses_one_named_file_in_full`),
`orchestrator_test`.

## Test

```powershell
python adapter/smoke_test.py        # boots RAG + adapter, 20 checks
cd adapter; python -m unittest acl_negative_test   # E03 negative suite: real RAG
                                                   # (both backends), mocked model
python sim/vivesecbox_sim.py sync   # drive the adapter with the real simulator
```
