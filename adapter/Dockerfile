# ViVeSec AIBox adapter — ViVeSec v2 front -> RAG contract translator.
#
# This is the AIBox external face on the Jetson. It is pure stdlib Python (it
# only imports the local corpus.py / meta.py), so it is a plain CPU runtime with
# NO pip install, NO poc/, NO NVIDIA runtime. It reaches the RAG service over the
# host loopback (RAG_URL), so run it with --network host alongside vivesec-rag.
#
# Build context = the adapter directory itself:
#   docker build -f adapter/Dockerfile -t vivesec-adapter adapter
FROM python:3.11-slim-bookworm

WORKDIR /app

# The mTLS bootstrap (J2) shells out to the openssl CLI, which the slim image
# does not ship (only libssl). Everything else stays pure stdlib / no pip.
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssl \
 && rm -rf /var/lib/apt/lists/*

# stdlib-only — just the adapter package.
COPY . ./adapter

# Drop any host-built bytecode caches to keep the image clean and arch-neutral.
RUN find . -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && find . -name '*.pyc' -delete

ENV PYTHONUNBUFFERED=1 \
    ADAPTER_HOST=0.0.0.0 \
    ADAPTER_PORT=8088 \
    RAG_URL=http://127.0.0.1:8090 \
    ADAPTER_TENANT_ID=default \
    ADAPTER_DRIVE_PREFIX=/storage/drives \
    ADAPTER_FEATURES= \
    ADAPTER_META_PATH=/data/adapter_meta.json \
    ADAPTER_WATCHDOG_SECONDS=90 \
    ADAPTER_WATCHDOG_ENABLED=auto \
    ADAPTER_STORAGE_MODE=off \
    ADAPTER_LUKS_DEVICE= \
    ADAPTER_LUKS_NAME=vivesec_data \
    ADAPTER_LUKS_MOUNT= \
    ADAPTER_LUKS_FORMAT= \
    ADAPTER_PKI_DIR=/data/pki \
    ADAPTER_TLS_HOSTNAME=local.aibox.vivesecbox.com \
    ADAPTER_TLS= \
    ADAPTER_SESSION_DIR=/data/sessions \
    ADAPTER_SESSION_IDLE=900 \
    ADAPTER_SESSION_TURNS=12 \
    ADAPTER_CHAT_POLICY=locked_hybrid \
    ADAPTER_LONGPOLL_SECONDS=25 \
    ADAPTER_LONGPOLL_MAX=55 \
    ADAPTER_JOBS_DIR=/data/jobs \
    ADAPTER_JOB_RETENTION_DAYS=7 \
    ADAPTER_JOB_MAX_PER_USER=50 \
    ADAPTER_JOB_QUEUE_MAX=5 \
    ADAPTER_MAX_CONTEXT_TOKENS=4000 \
    ADAPTER_UI_VERSION=latest \
    ADAPTER_DISCOVERY=auto \
    ADAPTER_DISCOVERY_ST=urn:vivesecbox-com:device:AIBox:1 \
    ADAPTER_RESET_PIN_MODE=off \
    ADAPTER_RESET_HOLD_SECONDS=5 \
    ADAPTER_FILES_DIR=/data/generated \
    ADAPTER_FEEDBACK_DIR=/data/feedback \
    ADAPTER_FEEDBACK_TRACES=200 \
    ADAPTER_PUTFILE_RETRIES=3 \
    ADAPTER_PUTFILE_RETRY_DELAY=1.0 \
    ADAPTER_PUTFILE_TIMEOUT=30 \
    OLLAMA_URL=http://127.0.0.1:11434 \
    ADAPTER_GEN_MODEL=qwen3.6:35b \
    ADAPTER_THINK=off \
    ADAPTER_NUM_PREDICT=2048 \
    ADAPTER_NUM_CTX=65536 \
    ADAPTER_ANALYZE_MAX_CONTEXT_TOKENS=60000 \
    ADAPTER_ANALYZE_MAX_CHARS=100000 \
    ADAPTER_ANALYZE_NUM_CTX=65536 \
    ADAPTER_GENERATE=auto \
    ADAPTER_ANSWER_LANG=

EXPOSE 8088

CMD ["python", "adapter/service.py"]
