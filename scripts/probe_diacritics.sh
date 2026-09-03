#!/usr/bin/env bash
# How much does dropping diacritics cost, per language? Users type "utazasi"
# for "utazási", "Reisekostenrichtlinie" as "Reisekostenrichtlinie", Danish
# "å/ø/æ" as "aa/oe/ae". Each pair below is the same question written both ways.
set -u
KEY=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-rag \
      | sed -n 's/^RAG_API_KEY=//p')

ask() {  # corpus, label, question
  printf '  %-12s %-46s ' "$2" "$(echo "$3" | cut -c1-46)"
  curl -fsS -m 30 -X POST http://127.0.0.1:8090/rag/search_context \
    -H 'Content-Type: application/json' -H "X-API-Key: $KEY" \
    -d "$(python3 -c "
import json,sys
print(json.dumps({'corpus_id': sys.argv[1], 'question': sys.argv[2], 'top_k': 3}))
" "$1" "$3")" \
    | python3 -c "
import json,sys
ctx = (json.load(sys.stdin).get('contexts') or [])
if ctx:
    print('%d talalat  legjobb %.3f  %s' % (len(ctx), ctx[0]['score'], ctx[0]['source_path'].split('/')[-1][:34]))
else:
    print('0 TALALAT')
"
}

HR=hr-6ba4fc3f
echo "=== magyar ==="
ask $HR "ekezettel"  "utazási költségtérítés napidíj"
ask $HR "ekezet nlk" "utazasi koltsegterites napidij"
ask $HR "ekezettel"  "Milyen a napidíj mértéke külföldi kiküldetésnél?"
ask $HR "ekezet nlk" "Milyen a napidij merteke kulfoldi kikuldetesnel?"

echo
echo "=== nemet ==="
ask $HR "umlauttal"  "Wie hoch ist die Tagespauschale für Auslandsreisen?"
ask $HR "ae/oe/ue"   "Wie hoch ist die Tagespauschale fuer Auslandsreisen?"
ask $HR "umlauttal"  "Reisekostenrichtlinie Erstattung Übernachtung"
ask $HR "ae/oe/ue"   "Reisekostenrichtlinie Erstattung Uebernachtung"

echo
echo "=== dan ==="
ask $HR "ae/oe/aa"   "Hvad er dagpengesatsen for rejser i udlandet?"
ask $HR "helyesen"   "Hvad er dagpengesatsen for rejser i udlandet?"
ask $HR "helyesen"   "rejsepolitik godtgørelse måltider"
ask $HR "oe/aa"      "rejsepolitik godtgoerelse maaltider"
