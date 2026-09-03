"""RAG orchestration: ingest -> retrieve -> (generate)."""
import glob
import os

import commands
import config
import embeddings
import llm
from chunking import chunk_text
from store import VectorStore


def ingest(data_dir=None, index_path=None):
    data_dir = data_dir or config.DATA_DIR
    index_path = index_path or config.INDEX_PATH
    use_ol = embeddings.use_ollama_embed()
    store = VectorStore()
    files = sorted(glob.glob(os.path.join(data_dir, "*.txt")))
    n_chunks = 0
    for fp in files:
        source = os.path.basename(fp)
        with open(fp, "r", encoding="utf-8") as f:
            text = f.read()
        chunks = chunk_text(text, config.CHUNK_WORDS, config.CHUNK_OVERLAP)
        vecs = embeddings.embed_many(chunks, use_ol)
        for i, (c, v) in enumerate(zip(chunks, vecs)):
            store.add(source, i, c, v)
            n_chunks += 1
    store.meta = {
        "embed_backend": embeddings.backend_name(use_ol),
        "n_sources": len(files),
        "n_chunks": n_chunks,
    }
    store.save(index_path)
    return store.meta, files


def load_store(index_path=None):
    index_path = index_path or config.INDEX_PATH
    if not os.path.exists(index_path):
        return None
    return VectorStore().load(index_path)


def format_citations(hits):
    lines = []
    for rank, (score, rec) in enumerate(hits, 1):
        snippet = rec["text"][:160].replace("\n", " ")
        lines.append("  %d. [%s #%d]  score=%.3f\n     %s..." % (
            rank, rec["source"], rec["chunk_index"], score, snippet))
    return "\n".join(lines)


def answer(query, top_k=None):
    top_k = top_k or config.TOP_K
    store = load_store()
    if store is None:
        return {"error": "No index found. Run:  python cli.py ingest"}

    idx_backend = store.meta.get("embed_backend", "")
    use_ol = embeddings.use_ollama_embed()
    # Keep query embedding in the same space the index was built with.
    if idx_backend.startswith("fallback") and use_ol:
        use_ol = False
    if idx_backend.startswith("ollama") and not use_ol:
        return {"error": "Index was built with Ollama embeddings but Ollama is "
                         "unavailable now. Re-run:  python cli.py ingest"}

    cmd, spec, rest = commands.parse(query)
    retrieval_query = rest if rest else query
    qvec = embeddings.embed_one(retrieval_query, use_ol)
    hits = store.search(qvec, top_k)
    sources = sorted({rec["source"] for _, rec in hits})

    result = {
        "command": cmd or "(ask)",
        "label": spec["label"],
        "hits": hits,
        "sources": sources,
        "meta": store.meta,
    }

    if spec["shape"] == "search":
        result["mode"] = "retrieval-only"
        result["answer"] = None
        return result

    context_tagged = "\n\n".join(
        "[%s #%d] %s" % (rec["source"], rec["chunk_index"], rec["text"])
        for _, rec in hits
    )
    chunk_texts = [rec["text"] for _, rec in hits]
    ans, backend = llm.answer(spec["system"], rest, context_tagged, chunk_texts, spec["shape"])
    result["mode"] = "rag-generate"
    result["answer"] = ans
    result["llm_backend"] = backend
    return result
