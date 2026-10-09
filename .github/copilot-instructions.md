# ViVeSec AI Box — Copilot instructions

The user communicates in Hungarian; reply in Hungarian. Code, identifiers, code comments and
commit messages are in English.

## What this repo is

This is an on-premise document Q&A appliance (the "AI Box") that runs on an NVIDIA Jetson
(JetPack 6). It pairs with the customer's **ViVeSecBox**, which owns users, drives and file sync. All
AI processing is local; the appliance has no cloud LLM.

```text
ViVeSecBox / web UI ──ViVeSec v2 API──▶ adapter ──RAG contract──▶ rag_service ──▶ Ollama (bge-m3 embed)
                                          │
                                          ├──▶ Ollama (generation)
                                          └──◀ WebSocket file channel (wsfs) ◀── ViVeSecBox
```

| Path | Role |
| --- | --- |
| `adapter/` | External face of the box, written in **pure stdlib Python** (`ThreadingHTTPServer`, no pip dependencies). Handles scope/ACL, chat profiles, prompts and generation, sessions, durable jobs, the scheduler, exports, audit/feedback, mTLS, SSDP discovery and wsfs. Entry point: `adapter/service.py`. |
| `rag_service/` | Internal extract → index → retrieve service (JSON, or SQLite + sqlite-vec + FTS5). It returns context, never the final answer. Dependencies: `rag_service/requirements.txt`. Imports chunking and embedding helpers from `poc/`. |
| `src/` | Web UI: React 19, TypeScript, TanStack Start/Router, Vite, Tailwind 4, shadcn/Radix. Main screen: `src/components/vivesec/ViveSecApp.tsx`. Model-output parsing: `live-parse.ts`. i18n: `i18n.tsx`, `i18n-data.ts`. Adapter clients: `src/lib/rag/adapter-client*.ts`. UI-facing operations: `src/lib/api/rag.functions.ts`. |
| `sim/` | ViVeSecBox and drive simulators for integration testing. |
| `scripts/jetson/` | Numbered install, deploy and diagnostic scripts for the box (the JetPack 6 installation kit). |
| `harness/`, `demo-corpus/` | Retrieval-quality evaluation and a synthetic test corpus (dev only, not shipped). |
| `poc/`, `rag-engine/`, `rag_service_colearn/` | Earlier prototypes. `rag_service` still imports `poc/`; do not delete it. |

The UI ships in two independent forms:

- an SSR Node server: `npm run build` → `.output/`;
- a static embed bundle loaded inside the ViVeSecBox: `npm run build:embed` → `dist/client`.

Changing one does not update the other.

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
- The Jetson has unified memory, so heavy GPU work (generation, embedding) is serialized through the
  adapter scheduler. Do not bypass it.

## Build and test

Python tests use `unittest` with flat module imports, so run them **from the module directory**:

```powershell
Push-Location adapter; python -m unittest discover -p "*_test.py"; Pop-Location
Push-Location rag_service; python -m unittest discover -p "*_test.py"; Pop-Location
# single module, e.g.:
Push-Location adapter; python -m unittest chat_policy_test; Pop-Location
```

Frontend:

```powershell
npx tsc --noEmit
npm run lint          # whole repo; separate pre-existing issues from new ones
npm run build         # SSR build
npm run build:embed   # embed bundle + scripts/embed-config.mjs check
```

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
- Configuration is driven by environment variables (`ADAPTER_*`, `RAG_*`, `VIVESEC_*`). Document
  each new variable in `adapter/README.md` or the developer documentation, with its default and its
  fail-safe behaviour.
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
