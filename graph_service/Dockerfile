# ViVeSec graph service (E26) — relationship extraction and queries.
#
# Internal only: listens on loopback (GRAPH_HOST=127.0.0.1) and is called by
# the adapter. The store is a separate Neo4j container (GRAPH_STORE=neo4j);
# extraction calls the model through the adapter's GPU broker (GRAPH_LLM_URL).
#
# Document text comes from the RAG extractor itself (rag_service/extract.py)
# so pages agree between search hits and graph evidence, and the access rule
# from rag_service/access.py; both are loaded by file path. Tesseract (hun+eng)
# reads scanned PDFs page by page.
#
# Build context = repository root (graph_service/ and rag_service/ needed):
#   docker build -f graph_service/Dockerfile -t vivesec-graph .
FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-hun \
 && rm -rf /var/lib/apt/lists/*

COPY rag_service/requirements.txt ./rag_service/requirements.txt
COPY graph_service/requirements.txt ./graph_service/requirements.txt
RUN pip install --no-cache-dir -r rag_service/requirements.txt -r graph_service/requirements.txt \
    pypdfium2 pillow

COPY rag_service/extract.py rag_service/access.py ./rag_service/
COPY graph_service ./graph_service

RUN find . -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && find . -name '*.pyc' -delete

ENV PYTHONUNBUFFERED=1 \
    GRAPH_HOST=127.0.0.1 \
    GRAPH_PORT=8095

CMD ["python", "graph_service/service.py"]
