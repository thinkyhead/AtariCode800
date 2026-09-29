#!/bin/sh
# Regenerate both Atari BASIC grammars and run every syntax gate.
#   tools/regen_and_test.sh [probe lines...]
# Prints: converter summary, per-file failure counts, open-region check on the
# corpus, then scopes for any probe lines given as arguments.
cd "$(dirname "$0")/.." || exit 1
S="${SUBLIME_ATARI:-$HOME/Projects/Retro/6502-Tools/Sublime/AtariTools}"
T="${TMPDIR:-/tmp}"

python3 tools/sublime_syntax_to_tm.py "$S/AtariBASIC.sublime-syntax" \
    syntax/ataribasic.tmLanguage.json --scope source.ataribasic --name "Atari BASIC" | tail -1 || exit 1
python3 tools/sublime_syntax_to_tm.py "$S/AtariBASIC_CI.sublime-syntax" \
    syntax/ataribasic-ci.tmLanguage.json --scope source.ataribasic.ci --name "Atari BASIC (CI)" >/dev/null || exit 1

for f in test/syntax/ataribasic.test.lst test/syntax/sublime-corpus.test.lst; do
    b=$(basename "$f")
    npx vscode-tmgrammar-test -g syntax/ataribasic.tmLanguage.json "$f" > "$T/syn_$b.log" 2>&1
    echo "$b failures=$(grep -c '^  at \[' "$T/syn_$b.log")"
done

open=$(node tools/scopes.js --doc test/syntax/sublime-corpus.test.lst 2>/dev/null | grep -c 'd=[1-9]')
echo "corpus lines leaving a region open: $open"

if [ $# -gt 0 ]; then
    node tools/scopes.js "$@" | sed 's/source\.ataribasic //; s/ctx\.\([a-z_0-9]*\)\.ataribasic/[\1]/g; s/  */ /g'
fi
