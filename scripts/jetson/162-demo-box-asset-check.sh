#!/usr/bin/env bash
# Text-mode check: which asset does the box serve, and is it the POST build?
set -u
cd /tmp
rm -f p.html as.txt one.js
curl -s http://127.0.0.1:8080/ -o p.html
grep -a -o '/assets/[^"]*\.js' p.html | sort -u > as.txt
echo "served assets:"
sed 's/^/  /' as.txt
echo
while read -r a; do
  curl -s "http://127.0.0.1:8080$a" -o one.js
  n=$(grep -a -c 'ui/file' one.js || true)
  v=$(grep -a -c 'Fetching the document' one.js || true)
  echo "  $a  uifile=$n viewer=$v bytes=$(wc -c < one.js)"
done < as.txt
