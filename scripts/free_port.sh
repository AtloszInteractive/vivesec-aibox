#!/usr/bin/env bash
# Print the first free TCP port in a range, so throwaway test instances never
# collide with a running service (a bound port answers /health and makes a dead
# container look alive).
set -eu
from="${1:-8093}"
to="${2:-8110}"
for port in $(seq "$from" "$to"); do
  if ! ss -tln | grep -qE "[:.]${port}[[:space:]]"; then
    echo "$port"
    exit 0
  fi
done
echo "nincs szabad port a(z) ${from}-${to} tartomanyban" >&2
exit 1
