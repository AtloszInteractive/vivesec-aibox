#!/usr/bin/env bash
# RAG redeploy on ANY box: builds from ~/rag-build, inherits the running
# container's env AND binds (they differ per box: the dev box mounts
# /data/rag:/data, the demo box /data/rag:/data/rag).
#
# STRICT=1 aborts when a file other than $EXPECTED changed against the running
# image. On a box whose image is several builds old that guard would always
# fire, so the default is to REPORT the diff and continue knowingly.
set -eu

TS=$(date +%Y%m%d-%H%M)
PREV="vivesec-rag:prev-$TS"
STRICT=${STRICT:-0}
EXPECTED=${EXPECTED:-"service.py store.py sqlite_store.py document_context_test.py"}
NAME=${NAME:-vivesec-rag}

echo "=== 1. rollback tag from the RUNNING image ==="
RUNNING=$(docker inspect -f '{{.Image}}' "$NAME")
docker tag "$RUNNING" "$PREV"
echo "  $PREV -> $(echo "$RUNNING" | cut -c8-19)"

echo
echo "=== 2. build ==="
cd "$HOME/rag-build"
docker build -q -f rag_service/Dockerfile -t vivesec-rag:new . >/dev/null
echo "  new image: $(docker inspect -f '{{.Id}}' vivesec-rag:new | cut -c8-19)"

echo
echo "=== 3. files that differ from the running image ==="
list_hashes() {
  docker run --rm --entrypoint sh "$1" -c \
    'for f in /app/rag_service/*.py /app/poc/*.py; do
       printf "%s %s\n" "${f##*/}" "$(sha256sum "$f" | cut -c1-16)"; done' 2>/dev/null | sort
}
list_hashes "$PREV" > /tmp/rag_prev.txt
list_hashes vivesec-rag:new > /tmp/rag_new.txt
CHANGED=$(diff /tmp/rag_prev.txt /tmp/rag_new.txt | awk '/^[<>]/ {print $2}' | sort -u | tr '\n' ' ')
echo "  changed: ${CHANGED:-<none>}"
if [ "$STRICT" = "1" ]; then
  for f in $CHANGED; do
    case " $EXPECTED " in
      *" $f "*) ;;
      *) echo "  ABORT (STRICT=1): unexpected change in $f"; exit 1;;
    esac
  done
fi

echo
echo "=== 4. unit tests in the new image ==="
fail=0
for t in document_context_test reaccent_test query_split_test extract_pages_test \
         skipped_test concurrency_test; do
  printf '  %-24s ' "$t"
  if out=$(docker run --rm -e VIVESEC_BACKEND=fallback vivesec-rag:new \
             python "/app/rag_service/$t.py" 2>&1); then
    echo "$out" | tail -1
  else
    echo "FAILED"; echo "$out" | tail -5 | sed 's/^/      /'; fail=1
  fi
done
[ "$fail" -eq 0 ] || { echo "  ABORT: tests failed; nothing was promoted."; exit 1; }

echo
echo "=== 5. promote + recreate (env AND binds inherited) ==="
docker tag vivesec-rag:new vivesec-rag:latest
envargs=()
while IFS= read -r e; do
  [ -n "$e" ] && envargs+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$NAME" \
         | grep -E '^(RAG_|OLLAMA_|VIVESEC_)')
bindargs=()
while IFS= read -r b; do
  [ -n "$b" ] && bindargs+=( -v "$b" )
done < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' "$NAME")
echo "  env:   ${#envargs[@]} entries"
echo "  binds: ${bindargs[*]}"

docker stop "$NAME" >/dev/null
docker rm "$NAME" >/dev/null
docker run -d --name "$NAME" --network host --restart unless-stopped \
  "${envargs[@]}" "${bindargs[@]}" vivesec-rag:latest >/dev/null
sleep 8

echo
echo "=== 6. verify ==="
docker inspect -f '  running={{.State.Running}}' "$NAME"
echo -n "  health: "; curl -fsS -m 20 http://127.0.0.1:8090/health || echo FAILED
echo
echo -n "  document_context (expect 400, not 404): "
curl -s -o /dev/null -w '%{http_code}\n' -m 20 -X POST \
  -H 'Content-Type: application/json' -d '{}' \
  http://127.0.0.1:8090/rag/document_context

echo
echo "ROLLBACK: docker rm -f $NAME && docker run -d --name $NAME --network host \\"
echo "  --restart unless-stopped <same env/binds> $PREV"
