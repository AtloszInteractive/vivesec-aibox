#!/usr/bin/env bash
# Production check after the reaccent deploy: the same question typed with and
# without accents must now reach the same document. The API key is read from
# the container and never printed.
set -eu
KEY=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-rag \
      | sed -n 's/^RAG_API_KEY=//p')

ask() {  # corpus, label, question
  printf '  %-14s %-44s ' "$2" "$(echo "$3" | cut -c1-44)"
  python3 - "$1" "$3" "$KEY" <<'PY'
import json, sys, urllib.request
corpus, question, key = sys.argv[1], sys.argv[2], sys.argv[3]
body = json.dumps({"corpus_id": corpus, "tenant_id": "default",
                   "question": question, "top_k": 3}).encode()
req = urllib.request.Request("http://127.0.0.1:8090/rag/search_context", data=body,
                             headers={"Content-Type": "application/json", "X-API-Key": key})
ctx = json.load(urllib.request.urlopen(req, timeout=60)).get("contexts") or []
if ctx:
    print("%d talalat  %.3f  %s" % (len(ctx), ctx[0]["score"],
                                    ctx[0]["source_path"].rsplit("/", 1)[-1][:36]))
else:
    print("0 TALALAT")
PY
}

HR=hr-6ba4fc3f
PUBLIC=public-aa2010a2
FINANCE=finance-114ed822

echo "=== magyar, ekezet nelkul (ez volt a hiba) ==="
ask $HR "ekezet nlk" "utazasi koltsegterites napidij"
ask $HR "ekezettel"  "utazási költségtérítés napidíj"
ask $HR "ekezet nlk" "Mennyi szabadsag jar egy munkavallalonak?"
ask $HR "ekezettel"  "Mennyi szabadság jár egy munkavállalónak?"
ask $PUBLIC "ekezet nlk" "Hany munkavallalot foglalkoztat a Voltara csoport?"
ask $PUBLIC "ekezettel"  "Hány munkavállalót foglalkoztat a Voltara csoport?"

echo
echo "=== kontroll: angol es negativ kerdes valtozatlan ==="
ask $FINANCE "angol"    "What was the gross margin in 2025?"
ask $FINANCE "negativ"  "What is the Q3 2026 revenue forecast?"
ask $PUBLIC  "negativ"  "What is the wifi password of the office router?"
