# ViVeSec AI Box — local RAG Proof of Concept

A small, **dependency-free** proof of concept that demonstrates the ViVeSec AI Box
can do **more than similarity search** on entry-class hardware: it does
**retrieval *and* generation** — fully offline — exactly like the mock app promises.

It answers the open question directly:

> "We may only be able to do a similarity search."

Wrong premise. Similarity search only **finds** passages; this PoC adds a small
**quantized LLM** on top to **generate** summaries, memos and reports from the
retrieved context. On a Jetson Orin Nano 8GB "Super" that generative model runs at
~25–43 tok/s (INT4). This PoC proves the *architecture* today, on your laptop,
then runs **unchanged** on the Jetson.

---

## What it does

```
            ┌─────────────────────────── ViVeSec AI Box (offline) ──────────────────────────┐
  query ──▶ │  parse slash-command  ─▶  embed query  ─▶  vector search (top-k)               │
            │                                                   │                             │
            │                                                   ▼                             │
            │   /search  ─────────────────────────▶  cite passages (retrieval only)          │
            │   /summary /memo /legal /finance ...  ─▶  LLM generates over retrieved context  │
            └───────────────────────────────────────────────────────────────────────────────┘
                       all data stays on the ViVeSec Box — no cloud, no tokens
```

Two interchangeable backends, auto-detected:

| Layer | Production (Jetson) | Zero-install fallback (runs now) |
|-------|---------------------|----------------------------------|
| Embeddings | Ollama `bge-m3` (multilingual) | signed feature-hashing (stdlib) |
| Generation | Ollama `qwen2.5:3b` + grounding-guard | extractive sentence synthesis |
| Vector store | Qdrant / sqlite-vec | JSON + cosine (stdlib) |

The fallbacks let you **run and demo the whole pipeline with no installs at all**.
Flip on Ollama and the *same code* produces real generative answers.

---

## Run it now (no installs)

Requires only Python 3.9+ (standard library only — no `pip install`).

```bash
cd poc
python cli.py info        # shows detected backends
python cli.py ingest      # index poc/data/*.txt
python cli.py ask "/search 99.9% uptime service credits"
python cli.py ask "/summary last board meeting decisions and action items"
python cli.py ask "/finance Q4 revenue, EBITDA and variance vs plan"
python cli.py chat        # interactive REPL
```

In this mode you get real **similarity search + extractive answers**. The output is
labelled `fallback:extractive` so it's always clear which backend produced it.

---

## Enable real generative mode (Ollama)

```bash
# 1. Install Ollama (native app): https://ollama.com
# 2. Pull the entry-class models:
ollama pull qwen2.5:3b                       # generation (default · best grounding)
ollama pull bge-m3                           # multilingual embeddings (retrieval)
# Optional — most fluent Hungarian prose (weaker grounding), swap in via env var:
ollama pull cas/eurollm-1.7b-instruct-q8
# 3. Re-index with real embeddings, then ask:
cd poc
python cli.py ingest
python cli.py ask "/summary last board meeting decisions and action items"
python cli.py bench       # measures tok/s on YOUR machine
```

`info` will now show `ollama:…` for both backends, and answers are fully
generated (with a measured tok/s figure in the backend label).

> **No native install / low on system-drive space?** Use the **portable zip**
> instead of the installer and keep the models off your system drive:
> ```powershell
> # download ollama-windows-amd64.zip from the GitHub releases, unzip to e.g. G:\ollama
> $env:OLLAMA_MODELS = "G:\ollama\models"   # store models on a roomy drive
> G:\ollama\ollama.exe serve                # leave running in its own window
> G:\ollama\ollama.exe pull bge-m3
> G:\ollama\ollama.exe pull cas/eurollm-1.7b-instruct-q8
> ```

### Troubleshooting: generated text is garbage / never stops

If answers come back as random-token gibberish (e.g. `Hello┼Ťl One informe- Cac…`)
**and** queries seem to hang for minutes, Ollama is offloading the model to a GPU
it cannot drive correctly. This is common on laptops with **old integrated/discrete
GPUs** (e.g. Intel HD 5xx, AMD Radeon R5/R7) that Ollama tries to use over Vulkan.
The fix is to force **CPU-only** execution:

```powershell
# persistent (recommended) — applies to every future `ollama serve`:
[Environment]::SetEnvironmentVariable("OLLAMA_LLM_LIBRARY","cpu","User")
# or just for the current server session, set before starting serve:
$env:OLLAMA_LLM_LIBRARY = "cpu"
```

Restart `ollama serve`, then **re-run `python cli.py ingest`** (embeddings produced
on the broken GPU are also corrupt and must be rebuilt). Verify with
`curl http://localhost:11434/api/ps` → `size_vram` should be `0`. Expect a few tok/s
for a 1.7–3B model on a typical laptop CPU.

> On the **Jetson Orin Nano (CUDA)** you do the opposite — the GPU is required and
> works correctly, so never set this there.

---

## Hungarian / multilingual

**Retrieval is multilingual; generation has a grounding-guard.**

- **Retrieval — bge-m3**, a strong multilingual embedder, so similarity search
  finds the right passages from Hungarian questions over an English corpus.
- **Generation — qwen2.5:3b + grounding-guard** (default). The guard (in
  `llm.py`) forbids outside knowledge and invented figures, and makes the model
  **refuse** out-of-corpus questions: e.g. *"Mennyi volt a Q1 forgalom?"* →
  *"Erre nincs adat a dokumentumokban."* instead of a hallucinated number.
- **Extractive fallback is perfect in any language** — it *selects* source
  sentences instead of generating, so `/search` and extractive answers have zero
  grammar errors in Hungarian even with no model installed.

**A/B finding (entry-class models) — it's the language *mix*, not model size:**
retrieval works cross-lingually, but *generation* on a 1.7–3B model loses numeric
precision when the corpus and the question are in different languages.

| Setup | qwen2.5:3b + guard answer to "Q4 revenue?" |
|-------|---------------------------------------------|
| English corpus + **English** question | ✅ "EUR 4.82 million, up 18%" (exact) |
| English corpus + **Hungarian** question | ❌ wrong (but in-context) number — no longer *invents* (guard works), but mixes figures |
| **Hungarian** corpus + **Hungarian** question | ✅ "4,82 millió EUR – 18%" (exact) |

The third row is a control experiment (`data_hu/` + Hungarian question): the **same
model** that failed cross-lingually is **perfectly accurate same-language**. So the
entry-box answer to "multilingual or one-language-per-box?" is:

> **Keep generation single-language: one box per language, or translate documents
> into the box language at ingest time.** Both make the small model accurate.
> Cross-lingual generation needs a larger model (`qwen2.5:7b`, fits Jetson 8GB) or a
> translate-then-answer step.

EuroLLM gives the most fluent Hungarian prose but hallucinated figures even with the
guard. **For exact figures regardless of language, prefer `/search` (extractive,
verbatim + citation) — zero hallucination by construction.**

Ask in Hungarian directly:

```bash
python cli.py ask "/summary foglald össze az utolsó vezetőségi ülés döntéseit"
python cli.py ask "/finance mennyi volt a Q4 bevétel és az EBITDA?"
```

Force the answer language even for English sources:

```powershell
$env:VIVESEC_ANSWER_LANG = "Hungarian"
python cli.py ask "/summary last board meeting decisions"
```

Model alternatives (override with `VIVESEC_GEN_MODEL`):

| Model | Grounding | Hungarian prose | Note |
|-------|-----------|-----------------|------|
| `qwen2.5:3b` | ★ refuses out-of-corpus | weak (cross-lingual) | **default** · verified (CPU) · Jetson 3B sweet spot |
| `cas/eurollm-1.7b-instruct-q8` | weak (hallucinates) | ★ best/size | most fluent HU · all EU languages |
| `qwen2.5:7b` | ★ | good | best numeric accuracy · fits Jetson 8GB · slow on laptop CPU |
| `gemma2:2b` | medium | good | solid multilingual |
| `jobautomation/OpenEuroLLM-Hungarian` | — | ★ | Hungarian-focused but ~8 GB (7B-class) — Enterprise tier |

---

## Run on the Jetson Orin Nano 8GB Super

The code is identical — only the platform setup differs:

```bash
# JetPack 6, then put the board in its highest power mode:
sudo nvpmodel -m 2        # MAXN Super
sudo jetson_clocks

# Ollama has a native arm64/Jetson build:
curl -fsSL https://ollama.com/install.sh | sh
ollama pull cas/eurollm-1.7b-instruct-q8
ollama pull bge-m3

cd poc
python3 cli.py ingest
python3 cli.py bench      # ~25–43 tok/s for a 3B INT4 model; EuroLLM-1.7B is faster
python3 cli.py chat
```

**Published NVIDIA references (Orin Nano Super, INT4):** Llama 3.2 3B ≈ 43 tok/s ·
Phi 3.5 ≈ 38 · Gemma2 2B ≈ 35 · Qwen2.5 7B ≈ 22 · Llama 3.1 8B ≈ 19.
A 1.7B model such as EuroLLM runs faster still. Under real RAG (context +
embeddings) expect ~60–75% of these numbers.

---

## How this maps to the mock app

The mock's slash commands are implemented here as **prompt templates over the same
model** — which is exactly how the domain "AI Coworker" modules scale on one box:

| Mock command | PoC command | Mode |
|--------------|-------------|------|
| `/search` | `/search` | retrieval + citations only |
| `/summary` | `/summary` | RAG → generate |
| `/report` | `/report` | RAG → generate |
| `/memo` | `/memo` | RAG → generate |
| `/legal` `/finance` `/compliance` `/hr` | same | RAG → generate (per-persona system prompt) |

**Key point for the roadmap:** Legal / Finance / HR / Compliance are **not separate
models**. They are one small LLM + a per-module knowledge base (separate index) + a
system prompt. That is what makes the "Module 0–4" plan feasible in 8 GB of RAM.

---

## Connect to the ViVeSec frontend (Live AI toggle)

`poc/server.py` is a tiny **stdlib-only HTTP bridge** (no `pip`) that exposes
`rag.answer()` to the mock app, so the UI can switch from canned mock responses to
**real, locally-generated answers**.

```
browser ─▶ TanStack server fn (Node SSR) ─▶ poc/server.py ─▶ rag.answer() ─▶ Ollama
```

**Endpoints**

| Method | Path | Body | Returns |
|--------|------|------|---------|
| GET | `/api/health` | — | `{ ok, ollama_running, llm_available, gen_model, embed_backend, index_ready, mode }` |
| POST | `/api/ask` | `{ query, lang }` | `{ ok, answer, mode, backend, sources, citations[] }` |

`lang` is the UI language code (`EN`/`HU`/`DA`/`DE`); the bridge maps it to an
answer-language instruction so the reply always matches the selected UI language.

**Run the full stack (all local):**

```bash
# 1) Ollama (CPU-only on this laptop — see Troubleshooting)
G:\ollama\ollama.exe serve

# 2) RAG bridge (this PoC)
cd poc
python server.py                 # -> http://127.0.0.1:8000   (VIVESEC_PORT to change)
# Windows: just double-click poc\start-bridge.cmd (auto-finds Python, no venv)

# 3) the mock frontend (repo root)
npm run dev                      # -> http://localhost:8080
```

Then open the app and flip the **"Live AI"** switch in the header (top-right, next
to the language selector — like the *Drive* button it only shows on wide windows,
≥ `md`/768px):
- **off** (default) → original mock responses; the demo runs with no Python at all.
- **on** → free-text questions and the RAG commands (`/search /summary /report
  /memo /finance /legal /hr /compliance`) are answered by the local model; the
  rich UI-only demos (`/workflow`, `/presentation`, `/tour`, `/hardware`) stay mock.

Turning the switch on first pings `/api/health`; if the bridge isn't running it
shows a hint and stays off. If a live call fails mid-conversation the app falls
back to the mock answer, so the demo never breaks. Point the frontend at a
different bridge with the **`RAG_BACKEND_URL`** env var (default
`http://127.0.0.1:8000`); on the Jetson, frontend + bridge share one box.

**Known limitation:** the bridge answers over the PoC corpus (`poc/data/*.txt`),
which is separate from the mock app's on-screen Drive files. The answer and citation
snippets are real, but a citation chip won't open a Drive document yet (the source
IDs don't line up). Aligning the two corpora is the natural next step.

---

## Memory budget (8 GB)

| Component | RAM |
|-----------|-----|
| 3B Q4 generative model | ~3 GB (7B Q4 ≈ 5 GB) |
| Embedding model | ~0.5 GB |
| Vector DB + OS + context | ~1.5–2 GB |
| **Total** | fits → **1 active agent + RAG** |

Matches the app's "1–2 parallel agents". Seven parallel agents / 30B+ models belong
to the **Enterprise** tier (2,070 TOPS), not the entry box.

---

## From PoC to production (what to swap)

- **Embeddings:** fallback hashing → Ollama `bge-m3` (multilingual default; great
  for HU/DE/DA) or `nomic-embed-text` (English-only).
- **Vector store:** JSON cosine → **Qdrant** or **sqlite-vec** (persistent, scalable).
- **Documents:** the `.txt` samples → real PDFs/DOCX from the ViVeSec Box drive
  (add `pypdf` for extraction).
- **Serving:** `rag.answer()` is already wrapped by **`poc/server.py`** (stdlib
  `http.server`, no dependency) — the mock front-end's **Live AI** toggle calls it.
  Swap for FastAPI/Uvicorn only if you need async throughput or many clients.
- **Modules:** one index + system prompt per domain; route by the slash command.

---

## Files

```
poc/
  cli.py            entrypoint (info / ingest / ask / chat / bench)
  server.py         stdlib HTTP bridge for the frontend (Live AI toggle)
  start-bridge.cmd  Windows one-click launcher for server.py (auto-finds Python)
  config.py         models, endpoints, chunking — all env-overridable
  rag.py            ingest → retrieve → generate orchestration
  embeddings.py     Ollama embeddings + dependency-free fallback
  llm.py            Ollama chat + extractive fallback
  store.py          JSON cosine vector store (swap for Qdrant in prod)
  ollama_client.py  stdlib-only Ollama HTTP client (urllib)
  chunking.py       text chunking / sentence splitting
  commands.py       slash-command prompt templates (mirrors the mock)
  data/             sample corpus mirroring the mock's Drive files
```

Everything here is offline by design. Nothing leaves the machine.
