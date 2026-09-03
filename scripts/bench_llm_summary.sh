#!/usr/bin/env bash
# What would per-page / per-document LLM summaries actually cost on this box?
#
# PageIndexes currently derives the doc vector from normalize(text[:1200]) and
# the page vector from the first 700 chars. Replacing those with real LLM
# summaries is option (1) in CoLearn's proposal — this measures the price using
# the generation model that already runs on the AI Box.
#
# Ollama reports prompt_eval_* (prefill) and eval_* (generation) separately, so
# we can extrapolate to a whole corpus instead of guessing.
set -u
MODEL="${MODEL:-qwen2.5:14b}"
URL="${URL:-http://127.0.0.1:11434}"

# A realistic page: ~2600 characters, the size PageIndexes uses for a page.
PAGE_TEXT="$(python3 - <<'PY'
base = ("The contractor shall furnish all labor, materials and equipment required "
        "for the installation of the concrete paving described in Section 02510. "
        "Group VIII operators receive a base rate of $20.33 per hour with a fringe "
        "benefit contribution of $5.27. Zone differentials apply beyond a 15 mile "
        "radius measured from the Big I interchange. ")
print((base * 8)[:2600])
PY
)"

run_one() {  # $1 = label, $2 = prompt
  local payload out
  payload=$(PROMPT="$2" python3 - <<'PY'
import json, os
print(json.dumps({
    "model": os.environ["MODEL"],
    "prompt": os.environ["PROMPT"],
    "stream": False,
    "options": {"num_predict": 120},
}))
PY
)
  out=$(curl -s -m 600 -X POST "$URL/api/generate" -H 'Content-Type: application/json' -d "$payload")
  printf '%s' "$out" | LABEL="$1" python3 - <<'PY'
import json, os, sys
d = json.load(sys.stdin)
ns = 1e9
print(f"{os.environ['LABEL']:16s} "
      f"prefill {d.get('prompt_eval_count', 0):5d} tok / {d.get('prompt_eval_duration', 0)/ns:6.2f} s   "
      f"gen {d.get('eval_count', 0):4d} tok / {d.get('eval_duration', 0)/ns:6.2f} s   "
      f"OSSZ {d.get('total_duration', 0)/ns:6.2f} s")
PY
}

export MODEL
echo "modell: $MODEL   (host Ollama, GPU)"
echo
run_one "warmup" "Summarize: hello world."
run_one "oldal-summary" "Summarize the following page in two sentences:

${PAGE_TEXT}"
run_one "oldal-summary#2" "Summarize the following page in two sentences:

${PAGE_TEXT}"
