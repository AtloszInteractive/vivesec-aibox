# ViVeSec AI Box — Copilot instructions

The user communicates in Hungarian; reply in Hungarian. Code, identifiers, code comments and
commit messages are in English.

## What this repo is

This is an on-premise document Q&A appliance (the "AI Box"). All AI processing is local; the
appliance has no cloud LLM. It runs in two deployments:

- **Matrix AI on an NVIDIA DGX Spark** (GB10, 128 GB unified memory, arm64, DGX OS) — the current
  target. The Spark is shared with the partner's **Dokumentum AI** (ViVeTech), which owns the
  pairing with the **ViVeSecBox** (ports 80/443/8443) and the Ollama on `127.0.0.1:11434`. The
  Matrix runs standalone (local users, `auth_service/`); the ViVeSecBox embeds its UI through a
  bootloader and reaches it over its proxy tunnel under `/api/v1/matrix/*` on port 7476.
- **The original paired box on an NVIDIA Jetson** (JetPack 6): the ViVeSecBox owns users, drives and
  file sync. Still supported, but no longer the main target.

```text
ViVeSecBox / web UI ──ViVeSec v2 API──▶ adapter ──RAG contract──▶ rag_service ──▶ Ollama (bge-m3 embed)
                                          │
                                          ├──▶ Ollama (generation)
                                          ├──▶ graph_service ──▶ Neo4j (relationship analysis)
                                          └──◀ WebSocket file channel (wsfs) ◀── ViVeSecBox (Jetson only)

Spark: ViVeSecBox tunnel ─mTLS─▶ :7476 nginx front ─▶ matrix_tunnel/gateway.py ─▶ adapter /api/v1/ui/*
                                                        └─▶ auth_service /auth/tunnel/token (VVS-User → local user)
```

| Path | Role |
| --- | --- |
| `adapter/` | External face of the box, written in **pure stdlib Python** (`ThreadingHTTPServer`, no pip dependencies). Handles scope/ACL, chat profiles, prompts and generation, sessions, durable jobs, the scheduler, exports, audit/feedback, mTLS, SSDP discovery and wsfs. Entry point: `adapter/service.py`. |
| `rag_service/` | Internal extract → index → retrieve service (JSON, or SQLite + sqlite-vec + FTS5). It returns context, never the final answer. Dependencies: `rag_service/requirements.txt`. Imports chunking and embedding helpers from `poc/`. |
| `auth_service/` | Standalone mode only (E27): local users, scrypt passwords, TOTP, sessions and the short-lived HS256 identity tokens the adapter verifies. Stdlib-only, loopback-only, called by the web UI server with a service key. Also the tunnel sign-in of ViVeSecBox users (`tunnel.py`, `manage.py tunnel-map`). |
| `graph_service/` | Relationship analysis (E26): extraction into Neo4j, domain profiles (e.g. `bv`), structured imports. Internal, called by the adapter; its own `requirements.txt` and Dockerfile. |
| `matrix_tunnel/` | Spark only: the tunnel gateway between the nginx front (ViVeSecBox client certificate) and the adapter. Serves the bootloader's app bundle. Stdlib-only. |
| `matrix_boot/` | Spark only: the small bootloader app the ViVeSecBox hosts; it loads the single-file Matrix UI (`npm run build:matrix`) through the FrameSocket. |
| `matrix_updater/` | Spark only: host-side update client (TUF, `python-tuf`), runs as root under systemd; checks, downloads, installs what an administrator approved, rolls back. |
| `src/` | Web UI: React 19, TypeScript, TanStack Start/Router, Vite, Tailwind 4, shadcn/Radix. Main screen: `src/components/vivesec/ViveSecApp.tsx`. Model-output parsing: `live-parse.ts`. i18n: `i18n.tsx`, `i18n-data.ts`. Adapter clients: `src/lib/rag/adapter-client*.ts`. UI-facing operations: `src/lib/api/rag.functions.ts`. |
| `sim/` | ViVeSecBox and drive simulators for integration testing. |
| `scripts/spark/` | The Spark installation kit: `spark_install.py` (phases, `--resume`/`--only`, `matrix.conf`), `pack_kit.py`, `pack_release.py` (prebuilt images on a build Spark), `usb/` (first installation from a USB stick). See `scripts/spark/README.md`. |
| `scripts/release/` | Release identity and configuration register (E01), and `matrix_repo.py`, the signed update repository (TUF) the releases are published into. |
| `scripts/jetson/` | The Jetson (JetPack 6) installation kit. Some of its scripts are reused by the Spark installer (`install/10-build-images.sh`, `standalone/auth_deploy.py`, `standalone/clamav_deploy.py`): keep them working for both. |
| `harness/`, `demo-corpus/` | Retrieval-quality evaluation and a synthetic test corpus (dev only, not shipped). |
| `poc/`, `rag-engine/`, `rag_service_colearn/` | Earlier prototypes. `rag_service` still imports `poc/`; do not delete it. |

The UI ships in two independent forms:

- an SSR Node server: `npm run build` → `.output/`;
- a static embed bundle loaded inside the ViVeSecBox: `npm run build:embed` → `dist/client`;
- the Matrix app for the Spark bootloader: `npm run build:matrix` → `dist/matrix/{app,boot}`.

Changing one does not update the others.

## Non-negotiable invariants

- **Scope is decided server-side and fails closed.** The adapter computes the allowed drives on every
  request; it never caches them. The sources are, in order: the `VVS-Drive` / `VVS-Other-Drives`
  headers, the `ADAPTER_ENTITLEMENTS` file (keyed by `VVS-User`), and the explicit all-drives switch.
  The client `drives` field may only narrow this set. An empty `drives` list means "no further
  narrowing"; an unauthorized drive is rejected. Paths containing `..` or pointing to another drive
  are rejected.
- At the RAG level, an empty corpus/file scope returns zero results, never "everything".
- `VVS-*` headers are trusted only from the integration channel. Base64 encoding is not
  authentication.
- Conversation history is isolated per user, per scope and per chat profile (grounded/hybrid).
  History from a wider scope must not leak into a narrower one.
- Retrieved chunk text stays verbatim; it backs exact quotes and citations.
- Ingest and query use the same embedding model. Never mix vectors from different models in one
  index.
- Unknown or invalid configuration falls back to the safer behaviour. For example, an unknown
  `ADAPTER_CHAT_POLICY` falls back to `locked_grounded`.
- Never put secrets in `VITE_*` variables, logs, docs or release bundles.
- RAG and Ollama endpoints stay internal (loopback or an internal network); the adapter is the only
  external surface.
- Both the Jetson and the Spark have unified memory, so heavy GPU work (generation, embedding) is
  serialized through the adapter scheduler (and its GPU broker for the graph). Do not bypass it.
- **The Spark is shared.** Never touch the Dokumentum AI's files, systemd units, containers,
  firewall table (`vivesec-prep-v2`) or ports. Its Ollama is used read-only: never pull or delete a
  model there. Our services listen on loopback; only the tunnel front (7476) faces the network.
- Through the tunnel, `VVS-*` headers are trusted only after the front verified the ViVeSecBox
  client certificate and the gateway checked the forward key. A ViVeSecBox user gets the roles and
  rooms of its local user, never more.
- Updates install only signed releases (TUF, root of the installed kit) and only after an
  administrator approved them; no version downgrade. The adapter only writes the request; the
  shared updates folder is treated as hostile by the root updater.

## Build and test

Python tests use `unittest` with flat module imports, so run them **from the module directory**:

```powershell
Push-Location adapter; python -m unittest discover -p "*_test.py"; Pop-Location
Push-Location rag_service; python -m unittest discover -p "*_test.py"; Pop-Location
Push-Location auth_service; python -m unittest discover -p "*_test.py"; Pop-Location
Push-Location graph_service; python -m unittest discover -p "*_test.py"; Pop-Location
Push-Location matrix_tunnel; python -m unittest discover -p "*_test.py"; Pop-Location
Push-Location scripts\spark; python -m unittest discover -p "*_test.py"; Pop-Location
Push-Location scripts\release; python -m unittest discover -p "*_test.py"; Pop-Location
# needs python-tuf: pip install -r matrix_updater/requirements.txt
Push-Location matrix_updater; python -m unittest discover -p "*_test.py"; Pop-Location
# single module, e.g.:
Push-Location adapter; python -m unittest chat_policy_test; Pop-Location
```

Frontend:

```powershell
npx tsc --noEmit
npm run lint          # whole repo; separate pre-existing issues from new ones
npm run build         # SSR build
npm run build:embed   # embed bundle + scripts/embed-config.mjs check
npm run build:matrix  # single-file Matrix app for the Spark bootloader
```

Some updater and installer tests (symlinks in container-writable folders) run only on POSIX and are
skipped on Windows; run them on the Spark when that code changes. The release, publishing and
upload steps for the Spark are in `scripts/spark/README.md`.

The smoke tests (`python adapter/smoke_test.py`, `python rag_service/smoke_test.py`) start services.
Run them only on free ports with isolated data directories, never against production endpoints or
data.

The local three-process dev setup (RAG on :8090, adapter on :8088, UI via `npm run dev`), with all
environment variables, is in section 10 of `docs/hu/ViVeSec_AIBox_Fejlesztesi_Dokumentacio.md`.

Unit tests use mocked RAG and model responses. They do not prove real-model answer quality or
large-corpus latency; say so when reporting results.

## Conventions

- `adapter/` must remain stdlib-only. Adding a third-party dependency there is an architectural
  decision: ask first.
- New adapter or RAG behaviour gets a `*_test.py` next to the module. Follow the existing tests:
  temporary stores, mocked HTTP and model calls.
- The UI supports EN, HU, DA and DE. Translate every new UI string into all of them. Keyed
  dictionaries live in `i18n.tsx`. In `i18n-data.ts`, the English source text maps to
  `[hu, da, de]` translations. Never build a sentence by string concatenation and then look up the
  whole result in this exact-match table.
- When the model output format changes, update `live-parse.ts`, the card rendering and the export
  together.
- Configuration is driven by environment variables (`ADAPTER_*`, `RAG_*`, `AUTH_*`, `GRAPH_*`,
  `VIVESEC_*`) and, on the Spark, `MATRIX_*` in `/etc/vivesec-matrix/matrix.conf`. Add each new
  variable to `scripts/release/config_registry.json` with its default and fail-safe behaviour, then
  run `python scripts/release/config_registry.py render` (it writes `CONFIGURATION.md`); document it
  in the module's README too.
- Repository files are LF (`.gitattributes`); convert files created on Windows before linting.
- Commit messages: a short imperative English summary, with an optional `area:` prefix (`adapter:`,
  `rag:`, `ui:`, `install kit:`).

## Documentation (local only)

`docs/` is git-ignored. It contains confidential commercial documents and exists only on the
development machine. Do not add it to git, and do not copy its contents into tracked files.

Key references:

- Current architecture and developer guide: `docs/hu/ViVeSec_AIBox_Fejlesztesi_Dokumentacio.md`.
- Epic specifications: `docs/ViVeSec_AIBox_Muszaki_Specifikacio_Melleklet1_20261001.md`.
- Epic list, estimates and gap analysis: `docs/ViVeSec_AIBox_Epic_Lista_20260923.md`,
  `docs/ViVeSec_AIBox_Epic_Becsles_20260923.md`, `docs/ViVeSec_AIBox_Termekspec_Gap_Analizis_20260922.md`.
- Milestones and customer prerequisites per epic: `docs/ajanlat/Melleklet3_M1_M2_Merfoldkovek_AJANLATI.md`.
- Acceptance criteria categories: `docs/ajanlat/Melleklet5_Elfogadasi_Kriteriumok_AJANLATI.md`.
- `docs/en/02-architecture-and-status.md` is a June 2026 snapshot from before the adapter existed.
  It is partly outdated.

## Epic workflow

Work is organised by epic (E01, E02, …), with one chat and one branch per epic.

- Branch: `epic/EXX-short-name`, created from an up-to-date `main`. The remote is `origin`
  (`AtloszInteractive/vivesec-aibox`, private).
- Before coding, read the epic's section in the specification and its prerequisites in the
  milestone annex.
- Some epics depend on open customer decisions: key ownership, the target customer profile,
  languages, golden flows and ViVeSecBox-side deliverables. Do not invent answers to these. Build
  behind configuration or interfaces, and state each assumption explicitly.
- Keep changes scoped to the epic. Preserve existing behaviour unless the epic requires a change.
- At the end of an epic, report what was implemented, how it was verified, the remaining gaps, and
  the assumptions to confirm with the customer.
- An epic goes to `main` with `git merge --squash`: one commit, a summary title ending in `(EXX)`, a
  bullet body, then the line `Squash of epic/<branch> (<short sha>).` before the trailer. Merge `main`
  into the epic branch and run the tests first. Work in one checkout; use a separate worktree only
  when two epics really run in parallel.
