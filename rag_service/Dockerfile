# ViVeSec RAG service — CoLearn/PageIndexes-compatible RAG over HTTP.
#
# Like the sim image this is CPU-only: embeddings are served by Ollama on the
# HOST (GPU) via the host network, so no NVIDIA container runtime is needed.
# The difference from sim is the extractor dependency (MarkItDown) for real
# PDF/DOCX/PPTX/XLSX ingestion.
#
# Build context = repository root (so both poc/ and rag_service/ are available):
#   docker build -f rag_service/Dockerfile -t vivesec-rag .
FROM python:3.11-slim-bookworm

WORKDIR /app

# MarkItDown's PDF/Office handlers are pure-Python wheels, so no system build
# toolchain is required for the default set. Install deps first for layer cache.
COPY rag_service/requirements.txt ./rag_service/requirements.txt
RUN pip install --no-cache-dir -r rag_service/requirements.txt

# Retrieval core (poc: embeddings + chunking + config, stdlib-only) and the RAG
# service itself.
COPY poc ./poc
COPY rag_service ./rag_service

# Drop any host-built bytecode caches to keep the image clean and arch-neutral.
RUN find . -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && find . -name '*.pyc' -delete

ENV PYTHONUNBUFFERED=1 \
    RAG_HOST=0.0.0.0 \
    RAG_PORT=8090 \
    RAG_INDEX_PATH=/data/rag_index.json \
    RAG_DEFAULT_TENANT=default \
    OLLAMA_URL=http://localhost:11434 \
    VIVESEC_BACKEND=auto

EXPOSE 8090

CMD ["python", "rag_service/service.py"]
