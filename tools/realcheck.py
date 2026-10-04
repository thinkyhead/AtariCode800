#!/usr/bin/env python3
"""Count invalid.* tokens in real Atari BASIC listings: a baseline grammar
vs the current one. Catches false errors the test corpus doesn't cover.

usage: tools/realcheck.py BASELINE.tmLanguage.json [ROOT]
  e.g. git show HEAD:syntax/ataribasic.tmLanguage.json > $TMPDIR/head.json
       tools/realcheck.py $TMPDIR/head.json
ROOT defaults to ~/Projects/Retro. Prints files whose count changed.
"""
import os, subprocess, sys
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
head = sys.argv[1]
root = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser('~/Projects/Retro')
files = []
for d, dirs, fs in os.walk(root):
    dirs[:] = [x for x in dirs if x not in ('node_modules', '.git', 'test')]
    for f in fs:
        if f.lower().endswith(('.lst', '.ulst')) and 'syntax_test' not in f:
            p = os.path.join(d, f)
            if os.path.getsize(p) < 60000:
                files.append(p)

def run(p, grammar):
    env = dict(os.environ, ALL='1')        # scopes.js --doc prints tokens only with ALL
    if grammar:
        env['GRAMMAR'] = grammar
    out = subprocess.run(['node', 'tools/scopes.js', '--doc', p], cwd=REPO,
                         env=env, capture_output=True, text=True).stdout
    return out.count('invalid'), out.count('\n')

tot = [0, 0, 0]
for p in sorted(files):
    a, n = run(p, head)
    b, _ = run(p, None)
    tot[0] += a; tot[1] += b; tot[2] += n
    if a != b:
        print('%4d -> %4d  %s' % (a, b, os.path.relpath(p, root)))
print('files=%d tokens=%d invalid: baseline=%d now=%d' % (len(files), tot[2], tot[0], tot[1]))
