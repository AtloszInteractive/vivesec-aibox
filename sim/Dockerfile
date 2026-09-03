# ViVeSec AIBox mock + simulator — CPU-only container for the Jetson.
#
# Embeddings are served by Ollama on the HOST (GPU), so this image stays a
# plain CPU Python runtime — it never needs the NVIDIA container runtime or the
# NVIDIA_VISIBLE_DEVICES/DRIVER_CAPABILITIES env. It reaches the host Ollama via
# the host network (see docker-compose.yml: network_mode: host).
#
# Build context = repository root (so both poc/ and sim/ are available):
#   docker build -f sim/Dockerfile -t vivesec-aibox-sim .
FROM python:3.11-slim-bookworm

WORKDIR /app

# stdlib-only Python — no pip install needed. Copy the retrieval core (poc) and
# the AIBox mock + simulator (sim).
COPY poc ./poc
COPY sim ./sim

# Drop any host-built bytecode caches to keep the image clean and arch-neutral.
RUN find . -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && find . -name '*.pyc' -delete

ENV PYTHONUNBUFFERED=1 \
    AIBOX_HOST=0.0.0.0 \
    AIBOX_PORT=8088 \
    AIBOX_INDEX_PATH=/data/aibox_index.json \
    OLLAMA_URL=http://localhost:11434 \
    VIVESEC_BACKEND=auto

EXPOSE 8088

CMD ["python", "sim/aibox_server.py"]
