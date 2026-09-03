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

## Test

```powershell
python adapter/smoke_test.py        # boots RAG + adapter, 20 checks
python sim/vivesecbox_sim.py sync   # drive the adapter with the real simulator
```
