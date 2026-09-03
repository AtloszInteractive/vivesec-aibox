#!/usr/bin/env bash
# Ship the query spelling repair to production.
#
# No reindex is needed: the vocabulary is built from the index that is already
# there. The previous image is kept under a dated tag, so rolling back is one
# docker run away.
set -eu

STAMP=$(date +%Y%m%d-%H%M)
PREV="vivesec-rag:prev-$STAMP"
EXPECTED="reaccent.py reaccent_test.py service.py sqlite_store.py store.py smoke_test.py"

echo "=== 1. visszaállási pont ==="
# The tag can already point elsewhere; what has to be preserved is the image
# the container is actually running.
RUNNING=$(docker inspect -f '{{.Image}}' vivesec-rag)
docker tag "$RUNNING" "$PREV"
echo "a futó image elmentve: $PREV ($(docker images --format '{{.ID}}' "$PREV" | head -1))"

echo
echo "=== 2. build ==="
cd /home/aibox/rag-build
cp ~/src/*.py rag_service/
docker build -q -f rag_service/Dockerfile -t vivesec-rag:latest . >/dev/null
echo "új image: $(docker images --format '{{.ID}}' vivesec-rag:latest)"

echo
echo "=== 3. mi változott az előző image-hez képest ==="
list_hashes() {  # image -> "name sha256"
  docker run --rm --entrypoint sh "$1" -c \
    'for f in /app/rag_service/*.py /app/poc/*.py; do
       printf "%s %s\n" "${f##*/}" "$(sha256sum "$f" | cut -c1-16)"; done' | sort
}
list_hashes "$PREV" > /tmp/prev.txt
list_hashes vivesec-rag:latest > /tmp/new.txt
CHANGED=$(diff /tmp/prev.txt /tmp/new.txt | awk '/^[<>]/ {print $2}' | sort -u)
echo "eltérő fájlok: ${CHANGED:-<nincs>}"
for f in $CHANGED; do
  case " $EXPECTED " in
    *" $f "*) ;;
    *) echo "VÁRATLAN ELTÉRÉS: $f -- LEÁLLÍTVA, a konténer nem lett lecserélve"; exit 1;;
  esac
done

echo
echo "=== 4. egységtesztek az új image-ben ==="
for t in reaccent_test query_split_test extract_pages_test skipped_test concurrency_test format_test; do
  printf '%-22s ' "$t"
  docker run --rm vivesec-rag:latest python "/app/rag_service/$t.py" 2>&1 | tail -1
done

echo
echo "=== 5. füstteszt az új image-ben (mind a 6 futás, saját példányon a :8097-en) ==="
docker run --rm --network host \
  -e OLLAMA_URL=http://localhost:11434 -e VIVESEC_BACKEND=auto \
  vivesec-rag:latest python /app/rag_service/smoke_test.py 2>&1 | tail -8

echo
echo "=== 6. konténer csere (az env-et a régiből örököljük, a kulcs nem íródik ki) ==="
ENV_ARGS=()
while IFS= read -r line; do
  ENV_ARGS+=(-e "$line")
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-rag \
         | grep -E '^(RAG_|OLLAMA_|VIVESEC_)')

docker stop vivesec-rag >/dev/null
docker rm vivesec-rag >/dev/null
docker run -d --name vivesec-rag --network host --restart unless-stopped \
  -v /data/rag:/data "${ENV_ARGS[@]}" vivesec-rag:latest >/dev/null

sleep 8
echo
echo "=== 7. ellenőrzés ==="
docker inspect -f 'fut: {{.State.Running}}  image={{.Image}}' vivesec-rag
curl -fsS -m 20 http://127.0.0.1:8090/health; echo
curl -fsS -m 20 http://127.0.0.1:8090/stats; echo
echo "adapter:"
curl -fsS -m 20 http://127.0.0.1:8088/api/v1/status; echo
echo
echo "visszaállás, ha kell:"
echo "  docker rm -f vivesec-rag && docker tag $PREV vivesec-rag:latest && bash ~/60-run-containers.sh"
