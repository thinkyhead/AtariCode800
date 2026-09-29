#!/usr/bin/env python3
"""Extract the ATASCII data from the Sublime plugin into JSON for VSCode.

Source of truth: ~/Projects/Retro/ATASCII (the Sublime ATASCII plugin).

Rather than retyping 128 Private Use Area codepoints into JavaScript -- where a
single wrong character would silently corrupt a listing -- we read them out of
``atascii.py`` and the three palette HTML files and emit generated JSON.

Re-run this whenever the Sublime plugin changes:

    python3 tools/extract_atascii.py [--atascii-dir ~/Projects/Retro/ATASCII]
"""

import argparse
import json
import os
import re
import sys

DEFAULT_SRC = os.path.expanduser('~/Projects/Retro/ATASCII')


def extract_invert_map(atascii_py):
    """Pull tr1/tr2 out of AtasciiInvertTextCommand and pair them up."""
    src = open(atascii_py, encoding='utf-8').read()

    # The strings contain PUA characters, quotes and backslash escapes, so match
    # the assignment lines and then evaluate just the literal.
    pairs = {}
    found = {}
    for name in ('tr1', 'tr2'):
        m = re.search(rf'^\s*{name}\s*=\s*(".*")\s*$', src, re.M)
        if not m:
            sys.exit(f'ERROR: could not find {name} in {atascii_py}')
        # ast.literal_eval handles the \" and \\ escapes exactly as Python does.
        import ast
        found[name] = ast.literal_eval(m.group(1))

    tr1, tr2 = found['tr1'], found['tr2']
    if len(tr1) != len(tr2):
        print(f'  NOTE: tr1 is {len(tr1)} chars, tr2 is {len(tr2)}; '
              f'zip() pairs the first {min(len(tr1), len(tr2))} '
              f'(this mirrors the Sublime plugin exactly)', file=sys.stderr)

    # Mirror the plugin's construction, including its zip() truncation.
    for a, b in zip(tr1, tr2):
        pairs[a] = b
        pairs[b] = a

    return pairs, tr1, tr2


def extract_palette(html_path):
    """Pull the characters out of a palette HTML file, preserving row layout."""
    html = open(html_path, encoding='utf-8').read()
    rows = []
    for div in re.findall(r'<div>(.*?)</div>', html, re.S):
        chars = re.findall(r'<a href="(.*?)">', div)
        chars = [c for c in chars if c]
        if chars:
            rows.append(chars)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--atascii-dir', default=DEFAULT_SRC)
    ap.add_argument('--out', default='media/atascii-data.json')
    args = ap.parse_args()

    src = os.path.expanduser(args.atascii_dir)
    if not os.path.isdir(src):
        sys.exit(f'ERROR: ATASCII plugin not found at {src}')

    invert, tr1, tr2 = extract_invert_map(os.path.join(src, 'atascii.py'))

    palettes = {}
    for key, fn in (('inverted', 'atascii-inverted.html'),
                    ('special',  'atascii-special.html'),
                    ('drawing',  'atascii-drawing.html')):
        path = os.path.join(src, fn)
        if not os.path.isfile(path):
            sys.exit(f'ERROR: missing palette {path}')
        palettes[key] = extract_palette(path)

    data = {
        '_generated_by': 'tools/extract_atascii.py',
        '_source': src,
        'invert': invert,
        'palettes': palettes,
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write('\n')

    print(f'-> {args.out}')
    print(f'   invert pairs : {len(invert)} entries '
          f'(tr1={len(tr1)}, tr2={len(tr2)})')
    for k, rows in palettes.items():
        print(f'   palette {k:9}: {len(rows)} rows, '
              f'{sum(len(r) for r in rows)} chars')


if __name__ == '__main__':
    main()
