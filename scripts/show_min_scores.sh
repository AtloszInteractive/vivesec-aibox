#!/usr/bin/env bash
# What relevance floor is each rag instance actually running with?
set -u
for c in vivesec-rag vivesec-rag-demo vivesec-rag-eval vivesec-rag-eval-sub vivesec-rag-pagefix2 vivesec-rag-t3probe; do
  printf '%-26s ' "$c"
  if ! docker inspect "$c" >/dev/null 2>&1; then
    echo "(nincs ilyen konteneR)"
    continue
  fi
  env=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" | grep '^RAG_MIN_SCORE=' || true)
  port=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" | grep '^RAG_PORT=' | cut -d= -f2)
  printf 'env=%-22s port=%-6s ' "${env:-<nincs beallitva>}" "${port:-?}"
  if [ -n "$port" ]; then
    curl -fsS -m 5 "http://127.0.0.1:$port/health" 2>/dev/null | tr -d '\n' | sed 's/.*"min_score": *\([0-9.]*\).*/health min_score=\1/' || printf 'health nem elerheto'
  fi
  echo
done
