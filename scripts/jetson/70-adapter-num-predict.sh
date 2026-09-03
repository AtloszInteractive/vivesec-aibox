#!/usr/bin/env bash
# Raise the adapter's generation length cap (ADAPTER_NUM_PREDICT).
#
# Why: the default 512 tokens truncates the longer spec outputs — an F3
# presentation outline stops around slide 5 of 8 and an F5 memo breaks
# mid-sentence. num_predict is a CEILING, not a target, so short answers are
# unaffected; only answers that would have been cut off get longer (and slower).
#
# Envs/volumes are captured from the RUNNING container via docker inspect, so
# every previously configured setting (including the RAG API key) survives the
# recreate without ever being printed.
set -e

NAME=vivesec-adapter
VALUE="${1:-1024}"

image=$(docker inspect -f '{{.Config.Image}}' "$NAME")
envargs=()
while IFS= read -r e; do
    [ -n "$e" ] && envargs+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$NAME" \
         | grep -v '^ADAPTER_NUM_PREDICT=' | grep -v '^PATH=' | grep -v '^LANG=' \
         | grep -v '^PYTHON_VERSION=' | grep -v '^PYTHON_SHA256=')
bindargs=()
while IFS= read -r b; do
    [ -n "$b" ] && bindargs+=( -v "$b" )
done < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' "$NAME")

echo "image=$image  binds=${#bindargs[@]}  envs=${#envargs[@]}  -> ADAPTER_NUM_PREDICT=$VALUE"
docker stop "$NAME" >/dev/null
docker rm "$NAME" >/dev/null
docker run -d --name "$NAME" --network host --restart unless-stopped \
    "${envargs[@]}" -e ADAPTER_NUM_PREDICT="$VALUE" "${bindargs[@]}" "$image" >/dev/null
sleep 6

echo "--- ADAPTER_NUM_PREDICT in the new container:"
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$NAME" | grep '^ADAPTER_NUM_PREDICT='
echo "--- adapter status (ui_ready must be true):"
curl -s -X POST http://127.0.0.1:8088/api/v1/status -d '{}' | head -c 160; echo
echo "--- rag reachable from the adapter path (/health):"
curl -s http://127.0.0.1:8090/health | head -c 120; echo
