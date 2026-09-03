#!/usr/bin/env bash
# Is GPU embedding actually worth wiring up on this Jetson?
#
# Single small embeds were previously measured as CPU ~= GPU (~540 ms), which
# suggests fixed per-request overhead dominates. Ingest, however, embeds whole
# documents in batches — that is compute-bound, so this compares BATCH throughput:
#   host ollama  (JetPack CUDA, GPU)  vs  colearn-rag ollama container (CPU)
set -u
MODEL="${MODEL:-bge-m3}"
HOST_URL="http://127.0.0.1:11434"
CONT_IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' colearn-rag-ollama-1 2>/dev/null)"
CONT_URL="http://${CONT_IP}:11434"

mk_payload() {  # $1 = number of texts
  python3 - "$1" "$MODEL" <<'PY'
import json, sys
n, model = int(sys.argv[1]), sys.argv[2]
text = ("Operating engineers group VIII zone 2 hourly wage schedule and fringe "
        "benefit rates for concrete mixers and paving equipment. ") * 6
print(json.dumps({"model": model, "input": [f"{i} {text}" for i in range(n)]}))
PY
}

bench() {  # $1 = label, $2 = url, $3 = batch size
  local payload; payload="$(mk_payload "$3")"
  # warm-up (model load must not be counted)
  curl -s -m 300 -X POST "$2/api/embed" -H 'Content-Type: application/json' \
       -d "$payload" -o /dev/null
  local t0 t1
  t0=$(date +%s.%N)
  local out; out=$(curl -s -m 300 -X POST "$2/api/embed" -H 'Content-Type: application/json' -d "$payload")
  t1=$(date +%s.%N)
  local n; n=$(printf '%s' "$out" | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("embeddings",[])))' 2>/dev/null || echo 0)
  printf '%-22s batch=%-4s %6.2f s  (%s vektor)\n' "$1" "$3" "$(echo "$t1 - $t0" | bc)" "$n"
}

echo "host ollama : $HOST_URL"
echo "cont ollama : $CONT_URL"
echo
for size in 1 16 64; do
  bench "HOST (GPU)" "$HOST_URL" "$size"
  bench "CONTAINER (CPU)" "$CONT_URL" "$size"
  echo
done
