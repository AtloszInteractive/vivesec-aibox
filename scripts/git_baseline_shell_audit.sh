#!/usr/bin/env bash
set -eu
root=${1:?usage: git_baseline_shell_audit.sh DIR}
count=0
while IFS= read -r -d '' script; do
  bash -n "$script"
  count=$((count + 1))
done < <(find "$root" -type f -name '*.sh' -print0)
echo "shell_syntax_ok files=$count"
