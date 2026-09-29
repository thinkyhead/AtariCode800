#!/usr/bin/env bash
#
# Sync the vendored Python tokenizer from its canonical home.
#
# python/ holds REAL files here, not a symlink: a .vsix cannot contain a link
# that escapes the extension root, and neither can a clean git checkout be
# relied on to have a sibling repo. The canonical source is the Sublime
# plugin's copy in the 6502-Tools repo; this script copies it in and reports
# whether anything changed.
#
# Usage:
#   tools/sync_tokenizer.sh            # copy from the default location
#   tools/sync_tokenizer.sh --check    # report drift, change nothing (exit 1)
#   SRC=/path/to/basic tools/sync_tokenizer.sh
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
DST="$ROOT/python"
SRC="${SRC:-$HOME/Projects/Retro/6502-Tools/Sublime/AtariTools/basic}"

# The runtime dependency set, verified by importing basic.py standalone.
# Anything not listed here (test_*.py, tkbasic.py, archive/, reports/) is
# development cruft and must NOT ship.
FILES=(
  __init__.py
  basic.py
  ataribasic_syntax.py
  ataribasic.py
  ataridefs.py
  atascii.py
  statement_table.py
)

if [[ ! -d "$SRC" ]]; then
  echo "error: source not found: $SRC" >&2
  echo "       set SRC=/path/to/AtariTools/basic" >&2
  exit 2
fi

check_only=0
[[ "${1:-}" == "--check" ]] && check_only=1

changed=0
for f in "${FILES[@]}"; do
  if [[ ! -f "$SRC/$f" ]]; then
    echo "error: missing from source: $f" >&2
    exit 2
  fi
  if [[ ! -f "$DST/$f" ]] || ! cmp -s "$SRC/$f" "$DST/$f"; then
    changed=1
    if (( check_only )); then
      echo "  DRIFT  $f"
    else
      mkdir -p "$DST"
      cp "$SRC/$f" "$DST/$f"
      echo "  copied $f"
    fi
  fi
done

if (( check_only )); then
  (( changed )) && { echo "python/ is out of date -- run tools/sync_tokenizer.sh"; exit 1; }
  echo "python/ is up to date with $SRC"
  exit 0
fi

(( changed )) || echo "python/ already up to date with $SRC"

# Prove the vendored copy actually works on its own.
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
printf '10 A$="HI"\n20 PRINT A$(1,2)\n' > "$tmp/t.LST"
if ( cd "$DST" && python3 -B basic.py --json "$tmp/t.LST" >/dev/null ); then
  echo "verified: vendored tokenizer runs standalone"
else
  echo "error: vendored tokenizer FAILED to run -- a dependency is missing" >&2
  exit 1
fi
