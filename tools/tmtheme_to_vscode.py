#!/usr/bin/env python3
"""Convert a TextMate .tmTheme (plist) into a VSCode color-theme JSON.

Keeps the author's palette: the tmTheme's global settings become the VSCode
workbench colors, and each scope rule carries over as a tokenColor.

Usage:
    python3 tmtheme_to_vscode.py Atari800.tmTheme themes/atari800-color-theme.json
"""
import json
import plistlib
import sys


def norm(color):
    """#RRGGBBAA -> #RRGGBB (VSCode tokenColors dislike alpha in some spots)."""
    if isinstance(color, str) and len(color) == 9 and color.startswith('#'):
        return color[:7]
    return color


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    src, dst = sys.argv[1], sys.argv[2]

    with open(src, 'rb') as f:
        theme = plistlib.load(f)

    settings = theme.get('settings', [])
    glob = {}
    rules = []

    for entry in settings:
        s = entry.get('settings', {})
        if 'scope' not in entry and not entry.get('name'):
            glob = s                     # the global settings block
            continue
        if 'scope' not in entry:
            continue
        style = {}
        if s.get('foreground'):
            style['foreground'] = norm(s['foreground'])
        if s.get('background'):
            style['background'] = norm(s['background'])
        if s.get('fontStyle'):
            style['fontStyle'] = s['fontStyle']
        if not style:
            continue
        rules.append({
            'name': entry.get('name', entry['scope']),
            'scope': entry['scope'],
            'settings': style,
        })

    bg = norm(glob.get('background', '#000000'))
    fg = norm(glob.get('foreground', '#CCCCCC'))
    sel = norm(glob.get('selection', '#264F78'))
    line = norm(glob.get('lineHighlight', bg))
    caret = norm(glob.get('caret', fg))

    out = {
        'name': theme.get('name', 'Atari 800'),
        'type': 'dark',
        'colors': {
            'editor.background': bg,
            'editor.foreground': fg,
            'editor.selectionBackground': sel,
            'editor.lineHighlightBackground': line,
            'editorCursor.foreground': caret,
            'editorWhitespace.foreground': norm(glob.get('invisibles', '#404040')),
            'sideBar.background': bg,
            'activityBar.background': bg,
            'panel.background': bg,
            'terminal.background': bg,
            'terminal.foreground': fg,
            'titleBar.activeBackground': bg,
            'statusBar.background': bg,
        },
        'tokenColors': rules,
    }

    with open(dst, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2)
        f.write('\n')

    print(f'{src} -> {dst}')
    print(f'  name       : {out["name"]}')
    print(f'  background : {bg}   foreground: {fg}')
    print(f'  tokenColors: {len(rules)} rules')
    return 0


if __name__ == '__main__':
    sys.exit(main())
