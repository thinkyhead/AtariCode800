#!/usr/bin/env bash
#
# Sync the vendored Python tokenizer with its canonical home.
#
# CANONICAL SOURCE OF TRUTH:
#   6502-Tools/Sublime/AtariTools/basic
#
# Everything else -- this repo's python/, any Sublime Packages deploy -- is a
# COPY and must match it. python/ holds REAL files here, not a symlink: a .vsix
# cannot contain a link that escapes the extension root, and a clean checkout
# cannot be relied on to have a sibling repo.
#
# Normal direction is canonical -> here. If you edit python/ directly (a
# convenience during extension work), --push sends those edits back to the
# canonical copy so the Sublime plugin and this extension never diverge. Test
# in BOTH editors after a push.
#
# Longer term the plan is to port the tokenizer to JavaScript so the extension
# can tokenize in-process with no Python, no shell-out, and no sync at all.
# Until that port is proven byte-for-byte against the ROM corpus, Python stays
# canonical and this script is the seam.
#
# Usage:
#   tools/sync_tokenizer.sh            # pull:  canonical -> python/
#   tools/sync_tokenizer.sh --check    # report drift either way, change nothing
#   tools/sync_tokenizer.sh --push     # push:  python/ -> canonical (asks first)
#   tools/sync_tokenizer.sh --diff     # show what differs
#   SRC=/path/to/basic tools/sync_tokenizer.sh
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
DST="$ROOT/python"
SRC="${SRC:-$HOME/Projects/Retro/6502-Tools/Sublime/AtariTools/basic}"

# The runtime dependency set, verified by importing basic.py standalone.
# Anything not listed here (test_*.py, tkbasic.py, basic-may/, archive/) is
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
  echo "error: canonical source not found: $SRC" >&2
  echo "       set SRC=/path/to/AtariTools/basic" >&2
  exit 2
fi

mode=pull
case "${1:-}" in
  --check) mode=check ;;
  --push)  mode=push ;;
  --diff)  mode=diff ;;
  '')      mode=pull ;;
  *) echo "error: unknown option: $1" >&2; exit 2 ;;
esac

for f in "${FILES[@]}"; do
  [[ -f "$SRC/$f" ]] || { echo "error: missing from canonical source: $f" >&2; exit 2; }
done

# Which files differ, and (for --check) which side is newer. Being told the
# direction matters: a stale copy and an unpushed edit look identical to cmp.
changed=()
for f in "${FILES[@]}"; do
  if [[ ! -f "$DST/$f" ]] || ! cmp -s "$SRC/$f" "$DST/$f"; then
    changed+=("$f")
  fi
done

case "$mode" in
  diff)
    if (( ${#changed[@]} == 0 )); then
      echo "no differences"
      exit 0
    fi
    for f in "${changed[@]}"; do
      echo "=== $f  (< canonical | > python/) ==="
      diff "$SRC/$f" "$DST/$f" || true
    done
    exit 0
    ;;

  check)
    if (( ${#changed[@]} == 0 )); then
      echo "python/ matches canonical: $SRC"
      exit 0
    fi
    for f in "${changed[@]}"; do
      newer=same
      [[ "$SRC/$f" -nt "$DST/$f" ]] && newer="canonical newer -- run: tools/sync_tokenizer.sh"
      [[ "$DST/$f" -nt "$SRC/$f" ]] && newer="python/ newer -- run: tools/sync_tokenizer.sh --push"
      printf '  DRIFT  %-24s %s\n' "$f" "$newer"
    done
    exit 1
    ;;

  push)
    if (( ${#changed[@]} == 0 )); then
      echo "nothing to push; python/ already matches canonical"
      exit 0
    fi
    echo "About to overwrite the CANONICAL tokenizer at:"
    echo "  $SRC"
    echo "with this repo's copies of:"
    printf '  %s\n' "${changed[@]}"
    echo
    echo "The Sublime plugin uses those files too. Re-run the 6502-Tools gates"
    echo "(test_corpus.py, regression_check.py) and test in Sublime afterwards."
    read -r -p "Proceed? [y/N] " reply
    [[ "$reply" == [yY]* ]] || { echo "aborted"; exit 1; }
    for f in "${changed[@]}"; do
      cp "$DST/$f" "$SRC/$f"
      echo "  pushed $f"
    done
    echo
    echo "Now run, in the 6502-Tools repo:"
    echo "  python3 -B agents/test_corpus.py && python3 -B agents/regression_check.py"
    exit 0
    ;;
esac

# --- pull (default) ---------------------------------------------------
for f in "${changed[@]}"; do
  mkdir -p "$DST"
  cp "$SRC/$f" "$DST/$f"
  echo "  copied $f"
done
(( ${#changed[@]} )) || echo "python/ already matches canonical: $SRC"

# Prove the vendored copy actually works on its own. A missing dependency is
# invisible until something imports it, so exercise a real tokenize.
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
printf '10 A$="HI"\n20 PRINT A$(1,2)\n' > "$tmp/t.LST"
if ( cd "$DST" && python3 -B basic.py --json "$tmp/t.LST" >/dev/null ); then
  echo "verified: vendored tokenizer runs standalone"
else
  echo "error: vendored tokenizer FAILED to run -- a dependency is missing" >&2
  exit 1
fi
