#!/usr/bin/env python3
"""Print converted grammar rules whose begin/match contains a substring.

    python3 tools/find_rule.py '//' [grammar.json]
    python3 tools/find_rule.py --repo comment      # a repository entry
"""
import json
import sys

args = sys.argv[1:]
repo = args and args[0] == '--repo'
if repo:
    args = args[1:]
needle = args[0]
g = json.load(open(args[1] if len(args) > 1 else 'syntax/ataribasic.tmLanguage.json'))

if repo:
    print(json.dumps(g['repository'].get(needle), indent=1))
    sys.exit()


def walk(o, path):
    if isinstance(o, dict):
        if needle in o.get('begin', '') or needle in o.get('match', ''):
            print('== ' + path)
            print(json.dumps(o, indent=1)[:1500])
        for k, v in o.items():
            walk(v, path + '/' + k)
    elif isinstance(o, list):
        for i, x in enumerate(o):
            walk(x, path + '[%d]' % i)


walk(g, '')
