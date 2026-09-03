#!/usr/bin/env bash
# Is the Q3-2026 answer actually in the corpus, or did the model invent it?
set -u
ROOT=~/demo-corpus/out/drives
echo "== 'not closed' occurrences =="
grep -rn 'not closed' "$ROOT" 2>/dev/null | head -6
echo
echo "== lines mentioning Q3 2026 with a number =="
grep -rniE 'q3[ _-]?2026' "$ROOT" 2>/dev/null | grep -E '[0-9]+[.,][0-9]' | head -8
echo
echo "== 15.6 anywhere in finance =="
grep -rn '15\.6' "$ROOT/finance" 2>/dev/null | head -6
