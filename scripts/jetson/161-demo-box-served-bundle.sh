#!/usr/bin/env bash
# Which bundle is actually served, and does it carry the POST document fetch?
set -u
cd /tmp
rm -f page.html assets.txt a*.js
curl -s http://127.0.0.1:8080/ -o page.html
echo "served assets:"
grep -o '/assets/[^"]*\.js' page.html | sort -u | tee assets.txt | sed 's/^/  /'
echo
i=0
while read -r a; do
  i=$((i + 1))
  curl -s "http://127.0.0.1:8080$a" -o "a$i.js"
  echo "  $a"
  echo "      ui/file mentions : $(grep -a -o 'ui/file' "a$i.js" | wc -l)"
  echo "      viewer strings   : $(grep -a -c 'Fetching the document' "a$i.js" || true)"
  # the POST body is built right next to the endpoint in the minified output
  echo "      JSON path body   : $(grep -a -o 'ui/file[^;]\{0,120\}' "a$i.js" | grep -c 'POST' || true)"
done < assets.txt
echo
echo "image now: $(docker inspect vivesec-ui --format '{{.Image}}' | cut -c8-19)"
echo
echo "=== last 20 ui/file lines in the adapter log ==="
docker logs -t --tail 2000 vivesec-adapter 2>&1 | grep 'ui/file' | tail -20
