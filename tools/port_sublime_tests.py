#!/usr/bin/env python3
"""Port Sublime's syntax_test_AtariBASIC.lst to vscode-tmgrammar-test format.

The Sublime corpus is the spec for what the grammar should emit, so it is
translated mechanically rather than rewritten by hand. Regenerate whenever the
Sublime test file changes:

    python3 tools/port_sublime_tests.py \
        ~/Projects/Retro/6502-Tools/Sublime/AtariTools/syntax_test_AtariBASIC.lst \
        syntax/ataribasic.tmLanguage.json \
        test/syntax/sublime-corpus.test.lst

The `R.` comment token is kept, so every caret column carries over verbatim.
Only scope names are rewritten, because the two harnesses match differently:

  * Sublime matches a scope by dotted PREFIX; vscode-tmgrammar-test requires an
    EXACT name. `keyword.print` becomes `keyword.print.ataribasic`.
  * A prefix with no exact counterpart (`invalid.error`) is ambiguous:
      - positive: mapped to the catch-all `invalid.error.syntax.ataribasic`,
        the scope of the grammar's syntax_error fallback.
      - negative: expanded to EVERY grammar scope under that prefix. A single
        exact-name exclusion would pass without testing anything.
  * Sublime's `R.^comment` on line 2 asserts the header line itself; the
    VSCode harness does not tokenize its header, so that assertion is dropped.
"""
import json
import re
import sys

SUFFIX = '.ataribasic'
ASSERT = re.compile(r'^(R\.)(\s*)(\^+|<-+)(\s*)(-?)\s*(.*)$')


def grammar_scopes(path):
    names = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ('name', 'contentName') and isinstance(v, str):
                    names.update(v.split())
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(json.load(open(path)))
    return names


def main(src, grammar, dst):
    names = grammar_scopes(grammar)
    lines = open(src).read().splitlines()
    out = ['R. SYNTAX TEST "source.ataribasic" "Ported from Sublime '
           'syntax_test_AtariBASIC.lst (tools/port_sublime_tests.py)"']
    unknown = set()

    def expand(scope, negative):
        exact = scope + SUFFIX
        if exact in names:
            return [exact]
        under = sorted(n for n in names if n.startswith(scope + '.'))
        if not under:
            unknown.add(scope)
            return [exact]          # keep it: fails loudly, as it should
        if negative:
            return under
        syntax = scope + '.syntax' + SUFFIX
        return [syntax if syntax in names else under[0]]

    for i, line in enumerate(lines):
        if i == 0 or (i == 1 and line.startswith('R.^')):
            continue
        m = ASSERT.match(line)
        if not m:
            out.append(line)
            continue
        tok, pad, marks, pad2, neg, rest = m.groups()
        scopes = []
        for s in rest.split():
            scopes += expand(s, bool(neg))
        body = ' '.join(scopes)
        # Harness syntax: `^ scope` or `^ - excluded`.
        out.append('%s%s%s %s%s' % (tok, pad, marks,
                                    '- ' if neg else '', body))

    open(dst, 'w').write('\n'.join(out) + '\n')
    if unknown:
        print('WARNING: no grammar scope for: ' + ', '.join(sorted(unknown)),
              file=sys.stderr)
    print('wrote %s (%d lines)' % (dst, len(out)))


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
