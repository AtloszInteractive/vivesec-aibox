# syntax=docker/dockerfile:1
#
# rag-engine — swappable retrieval-only container (spec §8, §9).
# Model weights are NOT baked in: mount them at RAG_MODEL_DIR (invariant 6).
# Runs internal-only on the docker network; the HÍD is the sole client.
#
# NOTE: for the Jetson AGX Orin target use an arm64 CUDA base
# (e.g. nvcr.io/nvidia/l4t-* / dustynv images) once the real engine pulls in
# torch + bge-m3. This slim base is enough for the contract skeleton.

FROM python:3.12-slim AS runtime
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RAG_HOST=0.0.0.0 \
    RAG_PORT=8081

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8081
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
