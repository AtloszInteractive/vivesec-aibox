#!/usr/bin/env bash
# Confirm the promoted UI bundle really carries today's client code.
set -u
cd /tmp
rm -f page.html assets.txt asset*.js
curl -s http://127.0.0.1:8080/ -o page.html
grep -o '/assets/[^"]*\.js' page.html | sort -u > assets.txt
echo "assets:"
cat assets.txt | sed 's/^/  /'
echo
i=0
while read -r a; do
  i=$((i + 1))
  curl -s "http://127.0.0.1:8080$a" -o "asset$i.js"
  v=$(grep -a -c 'Fetching the document' "asset$i.js" || true)
  r=$(grep -a -c 'What type of report' "asset$i.js" || true)
  c=$(grep -a -c 'Select the aspect of the report' "asset$i.js" || true)
  p=$(grep -a -c 'Retrieved passages' "asset$i.js" || true)
  echo "  $a  viewer=$v reportWizard=$r aspect=$c passages=$p  ($(wc -c < "asset$i.js") bytes)"
done < assets.txt
