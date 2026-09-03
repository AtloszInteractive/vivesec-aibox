# PageIndexes RAG Service Deployment

This directory is a self-contained Docker Compose deployment bundle. It starts:

- PageIndexes RAG Service
- PostgreSQL
- Qdrant
- Ollama
- optional Prometheus

## Start

```bash
cp .env.example .env
docker compose up -d
docker compose exec ollama ollama pull bge-m3
docker compose restart pageindexes-rag-service
```

With NVIDIA GPU support for Ollama:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
docker compose exec ollama ollama pull bge-m3
docker compose restart pageindexes-rag-service
```

## Check

```bash
curl http://localhost:8080/health
curl http://localhost:8080/ready
curl http://localhost:8080/openapi.json
bash smoke-test.sh
```

Open the generated API documentation:

- Swagger UI: `http://localhost:8080/docs`
- ReDoc: `http://localhost:8080/redoc`
- OpenAPI JSON: `http://localhost:8080/openapi.json`

The expected image includes field descriptions, examples, and documented error responses for the Drive Sync v1 endpoints:

- `POST /index/upsert/directory`
- `POST /index/upsert/file/check`
- `POST /index/upsert/file/content`
- `POST /index/upsert/file/content/{token}`
- `POST /index/drop/tree`

Quick OpenAPI check:

```bash
curl -fsS http://localhost:8080/openapi.json \
  | python -m json.tool \
  | grep -E 'Upsert directory metadata|Check file upload policy|Ingest base64 file content|Delete a source path tree'
```

## Configuration

Edit `.env` before starting on a new host.

Important settings:

- `PAGEINDEXES_IMAGE`: service image tag to run
- `API_KEY`: required `x-api-key` value for protected endpoints
- `EMBEDDING_MODEL`: Ollama embedding model, default `bge-m3`
- `EMBEDDING_DIM`: vector dimension, default `1024`
- `PAGEINDEXES_PORT`: host port, default `8080`

Keep `--workers 1` for `pageindexes-rag-service`. The tokenized upload flow uses in-process upload token storage.

## Updating The Service Image

The deployment bundle runs `PAGEINDEXES_IMAGE`; it does not build application code. When a new image has already been published, update `.env` if the tag changes, then pull and restart:

```bash
docker compose pull pageindexes-rag-service
docker compose up -d
```

## Monitoring

Prometheus is optional:

```bash
docker compose --profile monitoring up -d prometheus
```

Then open `http://localhost:9090`.
