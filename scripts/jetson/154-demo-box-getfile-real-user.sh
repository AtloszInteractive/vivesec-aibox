#!/usr/bin/env bash
# get-file with the REAL ViVeSecBox identities.
set -u
B=http://127.0.0.1:80/api/v1
DRIVE=$(printf '/storage/drives/aiboxdev/' | base64 | tr -d '=' | tr '+/' '-_')
FILE=${1:-'/storage/drives/aiboxdev/AI BOX TESZT/OFFICE ASSISTANT/README.md'}
ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$FILE")
echo "file: $FILE"
echo
for u in 'nano-box-dev@clarabot.com' '28744CZP27222' 'demo'; do
  code=$(curl -s -o /tmp/gf.bin -D /tmp/gf.hdr -w '%{http_code}' --max-time 90 \
          -H "VVS-Drive: $DRIVE" -H "VVS-User: $u" "$B/ui/file?path=$ENC")
  size=$(wc -c < /tmp/gf.bin)
  ct=$(grep -i '^content-type' /tmp/gf.hdr | tr -d '\r' | sed 's/^[Cc]ontent-[Tt]ype: //')
  printf "  %-28s HTTP %s  %6s bytes  %s\n" "$u" "$code" "$size" "$ct"
  if [ "$code" = "200" ]; then
    echo "      first bytes: $(head -c 100 /tmp/gf.bin | tr '\n' ' ')"
  else
    echo "      body: $(head -c 100 /tmp/gf.bin)"
  fi
done
