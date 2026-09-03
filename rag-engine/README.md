# rag-engine — cserélhető, retrieval-only RAG-konténer

Ez a **`rag-engine`** konténer váza. A **`/ingest` + `/search` kontraktust**
(`ViVeSec_AIBox_RAG_Interfesz_Spec.md`) valósítja meg, hogy a RAG-réteg külön
fejleszthető és a házon belüli `baseline` motorral **cserélhető** legyen.

A generálás (LLM) **nem** itt van — az a megosztott Ollama-konténerben fut. Ez a
réteg **csak visszakeres**: ingest + embedding + vektortár + hibrid keresés + reranker.

## Mit tartalmaz a váz

| Fájl | Szerep |
|---|---|
| `app/schemas.py` | A kontraktus pydantic-sémái (`/ingest`, `/search`, `/answer`, hibák) |
| `app/main.py` | FastAPI app — a stabil HTTP-végpontok (`/healthz`, `/stats`, `/ingest`, `/search`) |
| `app/engine.py` | `RagEngine` interfész + `BaselineEngine` **futtatható csontváz** (token-overlap, in-memory) |
| `app/security.py` | Belső szolgáltatás-token (`Authorization: Bearer …`) |
| `app/config.py` | Env-konfiguráció (`RAG_VECTOR_BACKEND`, `RAG_EMBED_MODEL`, `RAG_MODEL_DIR`, …) |

## Hol jön a valódi implementáció (CoLearn / házon belül)

A `BaselineEngine` csak azért működik, hogy a HTTP-kontraktus **ma is** végpontról
végpontra fusson. A valódi motor (spec §5.3) ezt cseréli le:

- **extract:** Docling (PDF/DOCX/XLSX → struktúra + táblák)
- **chunk:** struktúra-alapú 350–700 token, overlap 50–100 (spec §4.4)
- **embed:** bge-m3 (dense + sparse), súlyok `RAG_MODEL_DIR`-ből (mountolt kötet)
- **store:** sqlite-vec + FTS5 + `facts` **vagy** postgres+pgvector (spec §11.1)
- **search:** dense + sparse + BM25 → RRF → cross-encoder rerank (top-30 → top-5)

## Kötelező invariánsok (NEM eltérhető — spec §2)

1. **belső hálózat-only** (nincs kitett port, nincs nyilvános LLM-végpont)
2. `allowed_file_ids` **kemény pre-filter**; üres → 0 találat; a motor sosem bővíti
3. `chunk_text` **szó szerint** tárolva és visszaadva (T1-idézet)
4. **mindhárom kereső induljon** (dense + sparse + BM25)
5. ingest- és kérdés-embedding **ugyanazzal a modellel**
6. **modellsúlyok mountolt kötetből** (nem image-be sütve)

## Helyi futtatás (skeleton)

```bash
cd rag-engine
python -m venv .venv && . .venv/Scripts/activate   # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --port 8081

# füstteszt (külön shell):
python smoke_test.py
```

A konténerben: a `docker-compose.yml` `rag-engine` szolgáltatása indítja, belső
hálózaton, `RAG_INTERNAL_TOKEN`-nel és mountolt `models/` + `data/` kötetekkel.
