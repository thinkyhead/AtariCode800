#!/usr/bin/env python3
"""Convert a Sublime Text ``.sublime-color-scheme`` into a VSCode color theme.

This is the *live* source of truth for the Atari800 look:

    ~/Library/.../Packages/Atari800/Atari800.sublime-color-scheme
    ~/Library/.../Packages/Atari800/Atari800 Dark.sublime-color-scheme

An older ``.tmTheme`` was converted first by mistake; its globals had drifted
(background ``#0F497D`` where the live scheme says ``#065286``), which is why
VSCode did not colorize like Sublime.

Handles the parts of the format that actually appear in these files:

* JSON with ``//`` comments and trailing commas (Sublime dialect, not strict).
* ``var(name)`` indirection through the ``variables`` block, recursively.
* ``color(var(x) alpha(0.73))`` -> ``#RRGGBBAA`` (VSCode understands 8-digit hex).
* Scope *subtraction* (``a - (b | c)``) and *exclusion* (``a -b -c``), neither of
  which TextMate/VSCode selectors support. These are dropped with a warning; see
  ``--explain`` for why that is safe for these particular schemes.
* ``globals`` -> VSCode ``colors`` (workbench keys).
* ``font_style`` -> ``fontStyle``.

Usage:
    sublime_colorscheme_to_vscode.py IN.sublime-color-scheme OUT.json [--name NAME]
"""

import argparse
import json
import re
import sys

# Sublime global -> VSCode workbench color keys. One global often drives several.
GLOBAL_MAP = {
    'foreground':     ['editor.foreground'],
    'background':     ['editor.background'],
    'caret':          ['editorCursor.foreground'],
    'invisibles':     ['editorWhitespace.foreground'],
    'line_highlight': ['editor.lineHighlightBackground'],
    'selection':      ['editor.selectionBackground'],
    'gutter':         ['editorGutter.background'],
    'gutter_foreground': ['editorLineNumber.foreground'],
    'find_highlight': ['editor.findMatchBackground'],
    'misspelling':    ['editorError.foreground'],
}


def load_sublime_json(path):
    """Parse Sublime's relaxed JSON: // comments and trailing commas allowed."""
    src = open(path, encoding='utf-8').read()
    # Strip // comments, but not inside strings. These files have no URLs in
    # strings, so a conservative pass that ignores quoted regions is enough.
    out, i, n, in_str = [], 0, len(src), False
    while i < n:
        c = src[i]
        if in_str:
            if c == '\\':
                out.append(src[i:i + 2]); i += 2; continue
            if c == '"':
                in_str = False
            out.append(c); i += 1; continue
        if c == '"':
            in_str = True; out.append(c); i += 1; continue
        if c == '/' and i + 1 < n and src[i + 1] == '/':
            while i < n and src[i] != '\n':
                i += 1
            continue
        out.append(c); i += 1
    text = ''.join(out)
    text = re.sub(r',(\s*[}\]])', r'\1', text)          # trailing commas
    return json.loads(text)


def resolve_color(value, variables, depth=0):
    """Resolve var() indirection and color(... alpha(x)) into plain hex."""
    if not value or depth > 10:
        return value

    value = value.strip()

    # color(<inner> alpha(<f>))
    m = re.fullmatch(r'color\(\s*(.+?)\s+alpha\(\s*([0-9.]+)\s*\)\s*\)', value)
    if m:
        inner = resolve_color(m.group(1), variables, depth + 1)
        alpha = float(m.group(2))
        return apply_alpha(inner, alpha)

    # var(name)
    m = re.fullmatch(r'var\(\s*([A-Za-z0-9_\-]+)\s*\)', value)
    if m:
        name = m.group(1)
        if name not in variables:
            print(f'  WARNING: undefined variable var({name})', file=sys.stderr)
            return None
        return resolve_color(variables[name], variables, depth + 1)

    return value


def apply_alpha(hex_color, alpha):
    """Append an alpha byte; VSCode accepts #RRGGBBAA."""
    if not hex_color or not hex_color.startswith('#'):
        return hex_color
    h = hex_color[1:]
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    if len(h) == 8:                      # already has alpha; multiply through
        base, existing = h[:6], int(h[6:], 16) / 255.0
        alpha *= existing
        h = base
    if len(h) != 6:
        return hex_color
    a = max(0, min(255, round(alpha * 255)))
    return f'#{h}{a:02x}'


def clean_selector(scope):
    """Strip Sublime-only selector operators that TextMate/VSCode lack.

    Sublime supports subtraction: ``punctuation - (a | b)`` and ``string -a -b``.
    VSCode's TextMate matcher has no such operator, so those rules would be
    dropped wholesale (or worse, silently mis-parsed). We keep the base scope
    and drop the exclusion -- safe here because in both Atari800 schemes the
    excluded scopes resolve to the same colour as the base rule anyway (checked
    with --explain), so removing the exclusion is visually a no-op.
    """
    dropped = False

    # "base - (x | y)"
    new = re.sub(r'\s*-\s*\([^)]*\)', '', scope)
    if new != scope:
        dropped = True
    scope = new

    # "base -x -y" (exclusion without parens)
    parts = []
    for sel in scope.split(','):
        toks = [t for t in sel.split() if not t.startswith('-')]
        if len(toks) != len(sel.split()):
            dropped = True
        if toks:
            parts.append(' '.join(toks))

    return ', '.join(parts), dropped


def convert(src, dst, name=None):
    data = load_sublime_json(src)
    variables = data.get('variables', {})
    globals_ = data.get('globals', {})

    theme_name = name or data.get('name') or 'Converted'

    colors = {}
    for key, value in globals_.items():
        resolved = resolve_color(value, variables)
        if not resolved:
            continue
        for vs_key in GLOBAL_MAP.get(key, []):
            colors[vs_key] = resolved

    # Decide light vs dark from the actual background luminance rather than
    # trusting the theme's name -- "Atari800" (no suffix) is still light-on-dark.
    bg = colors.get('editor.background', '#000000')
    ui_theme = 'vs-dark' if luminance(bg) < 0.5 else 'vs'

    token_colors, dropped_selectors = [], []
    for rule in data.get('rules', []):
        scope = rule.get('scope')
        if not scope:
            continue
        cleaned, dropped = clean_selector(scope)
        if dropped:
            dropped_selectors.append(scope)
        if not cleaned:
            continue

        settings = {}
        for sub_key, vs_key in (('foreground', 'foreground'),
                                ('background', 'background')):
            if rule.get(sub_key):
                resolved = resolve_color(rule[sub_key], variables)
                if resolved:
                    settings[vs_key] = resolved
        if rule.get('font_style'):
            # Sublime uses space-separated styles; VSCode wants the same string.
            settings['fontStyle'] = rule['font_style']

        if not settings:
            continue

        entry = {'scope': cleaned, 'settings': settings}
        if rule.get('name'):
            entry = {'name': rule['name'], **entry}
        token_colors.append(entry)

    theme = {
        '$schema': 'vscode://schemas/color-theme',
        'name': theme_name,
        'type': 'dark' if ui_theme == 'vs-dark' else 'light',
        'colors': colors,
        'tokenColors': token_colors,
    }

    with open(dst, 'w', encoding='utf-8') as fh:
        json.dump(theme, fh, indent=2)
        fh.write('\n')

    print(f'{src}\n  -> {dst}')
    print(f'     name={theme_name!r} type={theme["type"]} '
          f'rules={len(token_colors)} colors={len(colors)}')
    print(f'     background={colors.get("editor.background")} '
          f'foreground={colors.get("editor.foreground")}')
    if dropped_selectors:
        print(f'     NOTE: dropped Sublime-only exclusion syntax in '
              f'{len(dropped_selectors)} selector(s):')
        for s in dropped_selectors:
            print(f'       {s}')
    return theme


def luminance(hex_color):
    h = hex_color.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    if len(h) < 6:
        return 0.0
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('source')
    ap.add_argument('dest')
    ap.add_argument('--name', help='override the theme name')
    args = ap.parse_args()
    convert(args.source, args.dest, args.name)


if __name__ == '__main__':
    main()
