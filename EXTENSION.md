# VSCode AtariCode800

The VSCode home for the **Atari 400/800** development suite: Atari BASIC,
6502 assembly, and C. Graphics tooling (charsets, player-missile graphics,
tilemaps, display lists, sound/music, bitmap graphics) comes next, via
WebViews.

**Scope: one platform, deliberately.** The Atari 2600 (bAtari BASIC, dasm,
Stella) and the 5200 are out. Other extensions cover those piecemeal; the
point here is a *full suite* tailored to the 8-bit computer line and its
hardware, with a specific vibe. Narrow focus is what makes the tooling
better than the generic alternatives.

Location: `~/Projects/Retro/AtariCode800` (its own repo; formerly
`~/Projects/Retro/6502-Tools/VSCode/AtariTools`)

## Language: JavaScript (for now)

Reviewed `~/Projects/Maker/VSCode/AutoBuildMarlin` as the style guide — plain
JS, `'use strict'`, modules required at the top, WebViews served from `.html`
files. AtariCode800 follows that shape (`src/extension.js` + modules). The user
is learning TypeScript and it may be a better long-term fit; when the move
happens, the modules here are small enough to port cleanly and the
`python/` tokenizer seam is language-agnostic.

## The single-source-of-truth rule

The tokenizer has ONE canonical home:

    6502-Tools/Sublime/AtariTools/basic        <- CANONICAL

Every other instance is a copy that must match it — this repo's `python/`, and
any Sublime Packages deploy. `python/` holds a **vendored copy**, not a symlink.

It used to be a symlink:

    VSCode/AtariTools/python -> ../../Sublime/AtariTools/basic

That worked in the old monorepo layout but cannot survive publication: a
`.vsix` cannot contain a link that escapes the extension root, and a clean
clone of this repo has no sibling to point at. So the seven files the tokenizer
actually imports are copied in, and `tools/sync_tokenizer.sh` keeps them
honest:

| command | direction |
|---|---|
| `tools/sync_tokenizer.sh` | canonical → `python/` (the normal direction) |
| `--check` | report drift and which side is newer; exit 1 |
| `--diff` | show what differs |
| `--push` | `python/` → canonical, after confirming |

Editing `python/` directly is allowed as a convenience, but it is a **loan, not
a fork**: run `--push` to return the change, then re-run the 6502-Tools gates
(`test_corpus.py`, `regression_check.py`) and test in Sublime, because the
plugin runs those same files. `--check` runs in the smoke suite, so drift
fails the tests rather than going unnoticed.

### Why Python is still canonical

Shelling out to Python is a real cost: an external dependency, a subprocess per
tokenize, and no tokenizing inside isolated WebViews. The plan is to port the
tokenizer to JavaScript so the extension can do all of it in-process.

That port is deferred deliberately. The Python implementation is validated
byte-for-byte against the real Atari BASIC ROM (currently 94.2% of the corpus,
12/21 files exact), and a rewrite before that reaches confidence would mean
debugging two unfinished implementations against each other. Python stays
canonical until the ROM corpus says it is trustworthy; then the JS port gets
the same corpus as its acceptance test.

Both editors run the SAME tokenizer. The extension shells out to `basic.py`
(`src/basic.js`), and `basic.py --json` returns everything the UI needs:

    python3 basic.py --json FILE.LST
      -> { listing, listing_abbr, vnt, vvt, lines:[{line,hex}], header, error }

## Launch

    atari-code            # dev host on ~/Projects/Retro/6502-Tools
    atari-code -i         # VSCode Insiders
    atari-code FILE       # open a specific file

`~/bin/atari-code` follows the `~/bin/abm-code` pattern
(`code --extensionDevelopmentPath=...`).

## Commands

- **AtariCode800: Build** — tokenize the active `.LST` to `.BAS` (staged in the
  configured H: directory, else alongside the source).
- **AtariCode800: Build and Run** — build, then launch atari800 (`-turbo -basic
  -run`). Keybinding: Cmd/Ctrl+Shift+B.
- **AtariCode800: Open BASIC Inspector** — the four-pane WebView.
  Cmd/Ctrl+Shift+I. Auto-refreshes on save.
- **AtariCode800: Got BASIC? Download a Program** — pick an Atari BASIC listing
  from `Crossover/AtariBasic` in the `6502-Tools` repo and download it into the
  workspace (`src/download.js`). Lists with one recursive `git/trees` request,
  fetches from `raw.githubusercontent.com`. A view button comes later.

  Two traps, both found only by running it live: the repo's default branch is
  `master`, not `main` (hence the `master` → `main` fallback and the
  `ataricode800.sourceBranch` setting), and git records the folder as
  `Crossover/AtariBasic` while a case-insensitive macOS checkout shows
  `AtariBASIC` — the GitHub API is case-sensitive, so folder matching is not.
  Downloads are written as raw bytes: a `.LST` is ATASCII with `$9B`
  terminators and a UTF-8 round trip would corrupt it.

## The BASIC Inspector WebView

The four panes from the Tkinter prototype (`tkbasic.py`), now in VSCode:

1. **Listing** — the re-LISTed program, with a **Compact** checkbox
   (`-a`/abbrev vs full). Toggling persists to `ataricode800.compactListing`.
2. **Tokenized (hex)** — per-line hex of the on-disk `.BAS` image, plus the
   14-byte header.
3. **VNT** — variable names.
4. **VVT** — variable types/values.

All data comes from `basic.py --json`, so the Inspector can never disagree
with the tokenizer. `src/inspector.js` renders it; `test/smoke.js` proves the
whole seam headlessly (20 checks, including a real ca65+ld65 build).

## File types

| Extension | Meaning |
|---|---|
| `.LST` | Atari BASIC listing in **ATASCII** |
| `.ULST` | the same listing in **Unicode** (easier to read/edit on a modern host) |
| `.BAS` | tokenized Atari BASIC program (the build output) |
| `.asm` `.s` `.inc` `.a65` | 6502 assembly |
| `.lnk` `.cfg` | ld65 linker config (the memory map) |

Both `.LST` and `.ULST` are BASIC *source* and tokenize identically; the
distinction is character encoding, not content. Note the Atari side only
knows `.LST` — the Sublime run script renames `.ULST` → `.LST` when staging
to the H: drive, and the VSCode build reduces either to the same 8.3 `.BAS`
name.

## Languages & grammars

Four grammars, all converted from the Sublime originals by
`tools/sublime_syntax_to_tm.py`:

| Grammar | Scope | Files |
|---|---|---|
| Atari BASIC | `source.ataribasic` | `.lst` `.ulst` |
| Atari BASIC (CI) | `source.ataribasic.ci` | (no extensions — pick via Change Language Mode) |
| Atari 6502 Assembly | `source.asm` | `.asm .s .inc .mac .a65` |
| LD65 Config | `source.ld65cfg` | `.lnk .cfg` |

### The CI (case-insensitive) variant

Real Atari BASIC accepts only uppercase keywords, so `source.ataribasic` is
strict: lowercase `print` gets no keyword scope, which is a useful mistake to
see. `AtariBASIC_CI.sublime-syntax` — named "AtariBASIC (loose)" upstream — is
the same grammar with `(?i)` applied to every keyword pattern, for reading
listings typed in lowercase.

The difference is exactly that, and the smoke suite asserts it: on
`20 print "HI"` the strict grammar scopes 0 keywords and the CI grammar
scopes 1.

It has a language id (`ataribasic-ci`) but deliberately claims **no file
extensions** — `.LST`/`.ULST` belong to the strict grammar, and claiming them
here would fight the default. Select it per-file with Change Language Mode.

A VSCode "AtariBASIC Syntax" extension already exists, but it was just a
straight conversion of the Sublime plugin. AtariCode800 does its own port
(`syntax/`) so it can track the Sublime grammar as it's improved.

## Build targets

BASIC goes through the Python tokenizer; assembly and C go to **cc65**.
`ataricode800.build` dispatches on the active document's language.

Assembly follows the Sublime `Atari 800` target
(`helper/Atari800-build.sh`) — the real workflow:

    ca65 $file -l $base.txt -o $base.o
    ld65 -o $base.bin -C $base.lnk $base.o atari.lib
    atari800 -atari -nobasic -run $base.bin

Two details that matter, both verified rather than assumed:

1. **The per-project linker config is not optional.** `$base.lnk` (else
   `.cfg`) decides the memory map, and Atari work is full of "this must live
   at $2000 / below the display list / in page 6" constraints. If it's
   missing, the build *offers to create one* from cc65's stock
   `atari-asm.cfg` rather than silently linking against a guess.
2. **`atari.lib` must be on the ld65 command line.** The stock config imports
   `__EXEHDR__` and `__AUTOSTART__`; without the library ld65 fails with
   "unresolved external", which reads like a source error but isn't.

A correct build looks like this — note the `$FFFF` binary-load header and the
load range:

    00000000: ffff 002e 072e a934 8dc6 024c 002e e002
                   ^^^^ ^^^^ load $2E00-$2E07

**Toolchain note:** the Sublime "Atari ca65" target runs
`ca65 -tatari -o $file_base_name.xex $file`, but `ca65` alone emits an *xo65
object file* — `file` reports "xo65 object, version 17". Naming it `.xex`
doesn't make it loadable; it still has to go through ld65. (C builds use
`cl65`, which drives cc65 + ca65 + ld65 in one step.)

## Grammar/theme conversion

`tools/sublime_syntax_to_tm.py` converts the Sublime grammars (1413-line
AtariBASIC, 6502) to TextMate JSON. It handles Sublime-isms the naive
converters miss:

- bare `=` keys (PyYAML `value` tag)
- `{{variable}}` interpolation
- `push`/`pop`/`meta_scope` -> begin/end regions
- Oniguruma-only regex: `\h` -> `[0-9a-fA-F]`, and bare `(?i)` -> `(?i:...)`.
  Note the nested case, which the naive fix gets wrong:
  `(?i:(LE[T.])?\s*((?i)[A-Z]...))` — the inner flag sits inside a scope that
  already applies it, so wrapping it would unbalance the parens. The converter
  tracks active flags and drops the redundant one.

All regexes across the four grammars are validated to compile in JS (the
engine VSCode actually uses) as part of conversion.

`tools/tmtheme_to_vscode.py` converts `Atari800.tmTheme` to a color theme,
keeping the authentic `#0F497D` background and 47 token rules.

Regenerate after editing a Sublime grammar:

    python3 tools/sublime_syntax_to_tm.py <Sublime>.sublime-syntax \
        syntax/<name>.tmLanguage.json --scope source.ataribasic --name "Atari BASIC"

**Known grammar gap:** the Sublime AtariBASIC syntax needs adjustment before
it perfectly matches the ABML rules, especially for complex nested expressions
(e.g. `IF A THEN IF B THEN ...`, parenthesized sub-expressions, mixed
comparison chains). The converter is faithful to whatever the Sublime grammar
says; fixing the grammar is Sublime-side work that then flows here.

## Settings

| key | default | meaning |
|---|---|---|
| `ataricode800.pythonPath` | `python3` | interpreter for the tokenizer |
| `ataricode800.basicToolPath` | (bundled/shared) | explicit path to basic.py |
| `ataricode800.emulatorPath` | `atari800` | emulator for Run |
| `ataricode800.hardDrivePath` | (source dir) | H: dir for staged .BAS |
| `ataricode800.turbo` | true | pass `-turbo` to atari800 |
| `ataricode800.compactListing` | false | default Inspector listing mode |

## Themes

Two themes, converted from the **live** Sublime source of truth at
`~/Projects/Sublime/Atari800` (the `thinkyhead/Atari800` repo):

| VSCode theme | From | Background |
|---|---|---|
| `Atari800` | `Atari800.sublime-color-scheme` | `#065286` |
| `Atari800 Dark` | `Atari800 Dark.sublime-color-scheme` | `#0a3357` |

Regenerate with:

    python3 tools/sublime_colorscheme_to_vscode.py \
        ~/Projects/Sublime/Atari800/Atari800.sublime-color-scheme \
        themes/atari800-light-color-theme.json

Both are `uiTheme: vs-dark` -- "Atari800" is the *lighter* of the two but is
still light-on-dark, so VSCode should use dark workbench chrome for both.

Sublime's selector *subtraction* (`punctuation - (a | b)`, `string -x -y`) has
no TextMate equivalent and is dropped by the converter. That is a visual no-op
for these two schemes -- the excluded scopes resolve to the same colour as the
base rule -- and `test/smoke.js` asserts it rather than taking it on faith.

## The Atari editor look

Verified empirically (launched VSCode, screenshotted, inspected the glyphs):
**`editor.fontFamily` IS language-overridable**, so a `.LST` renders in the
Atari Classic font while the rest of VSCode stays in your normal font. No
custom editor needed for the basic retro feel.

The extension ships this as `contributes.configurationDefaults` — defaults,
not forced settings, so anything you put in your own `settings.json` still
wins. Ported from `AtariBASIC.sublime-settings`:

| Setting | Value | Why |
|---|---|---|
| `editor.fontFamily` | `AtariClassic-Regular, 'Atari Classic', 'EightBit Atari', monospace` | the 8-bit glyphs |
| `editor.rulers` | `[38, 40, 76, 80, 114, 120, 254]` | the 40-column screen and its multiples |
| `editor.tabSize` | 1 | Atari BASIC has no indentation |
| `editor.autoIndent` | `none` | ditto |
| `editor.minimap.enabled` | false | a minimap on a 40-column program is absurd |
| `editor.detectIndentation` | false | stop VSCode second-guessing |

Assembly gets `tabSize: 8` to match the Sublime 6502 settings.

**Fonts are not bundled** (they're third-party): install *Atari Classic* or
*EightBit Atari* into `~/Library/Fonts`. Without them the chain falls back to
`monospace` and everything still works, just without the vibe. All three
names resolve as fixed-pitch on this machine (checked via `NSFont`);
`Atari Classic` and `AtariClassic-Regular` are the same face.

### The `atari.basic` scope — tried, didn't work

The stale **MarcinJozwikowski.atari-basic** extension (itself a conversion of
the same Sublime grammar) registers language id `atari.basic` for `.bas`. We
tried shipping a `"[atari.basic]"` block in this extension's
`configurationDefaults` to give those files the Atari look. **It did not work
in practice and has been removed.**

Why it looked fine in testing but wasn't: it was verified in an Extension
Development Host with a *clean* user profile. Extension
`configurationDefaults` sit at the very bottom of VSCode's settings
precedence — below user settings, which is exactly the point of "defaults" —
so a real profile with `editor.fontFamily` set at the user level silently
wins. The clean-profile test could never have surfaced that.

The lesson worth keeping: **don't try to configure scopes you don't own.**
If you want the look on `atari.basic` files, put it in your own
`settings.json`, where it outranks every extension default:

```jsonc
"[atari.basic]": {
  "editor.fontFamily": "AtariClassic-Regular, 'Atari Classic', monospace",
  "editor.rulers": [38, 40, 76, 80]
}
```

AtariCode800 only ships defaults for the language ids it declares
(`ataribasic`, `asm6502`).

**Heads-up on `.bas` collisions.** Several installed extensions claim `.bas`:

| Extension | Language id | Extensions |
|---|---|---|
| MarcinJozwikowski.atari-basic | `atari.basic` | `.bas` |
| billycharlton.atari-fastbasic | `basic` | `.bas` `.fb` |
| chunkypixel.atari-dev-studio | `7800basic`, `batariBasic` | `.bas` `.78b` `.bb` |

Which one wins on a given file is not deterministic, so if a `.bas` opens in
the wrong mode, set it explicitly (`Change Language Mode`) or pin it per
workspace with `files.associations`. AtariCode800 itself never claims `.bas` —
in this project `.bas`/`.BAS` is the *tokenized binary*, not source.

### Custom editor (possible, not yet built)

VSCode's `CustomTextEditor` API would allow going further than theming — a
true 40-column canvas, ATASCII glyph rendering including inverse video and the
control-character graphics, maybe a live screen preview. The tradeoff is that
a custom editor replaces the real text editor, so you lose find/replace,
multi-cursor, extensions, and Git gutters unless you rebuild them. Worth
prototyping *beside* the normal editor (as a preview pane) before considering
it as a replacement.

## ATASCII

Ported from the Sublime ATASCII plugin (`~/Projects/Retro/ATASCII`):

| Command | Keybinding |
|---|---|
| ATASCII: Insert Inverted Character | `ctrl+shift+a ctrl+shift+a` |
| ATASCII: Insert Special Character | `ctrl+shift+a ctrl+shift+s` |
| ATASCII: Insert Drawing Character | `ctrl+shift+a ctrl+shift+d` |
| ATASCII: Invert Selected Text | `ctrl+shift+a ctrl+shift+i` |

The 128 Private Use Area codepoints are **not retyped** into JavaScript -- one
wrong character would silently corrupt a listing. `tools/extract_atascii.py`
reads them out of the Sublime plugin into `media/atascii-data.json`, and the
smoke test compares our `invertText()` against Python running the Sublime
plugin's own mapping, byte for byte.

Re-run after changing the Sublime side:

    python3 tools/extract_atascii.py

### A bug inherited from the Sublime plugin

`tr2` in `atascii.py` contains `U+E0DF` **twice** (positions 27 and 95), so the
inversion map is not a clean involution: `U+E01B -> U+E0DF`, but `U+E0DF` maps
back to `_` (`U+005F`). One character fails to round-trip. The VSCode port
reproduces this faithfully rather than quietly "fixing" it, so both editors
behave identically -- but it is a real bug worth correcting in the Sublime
plugin, at which point re-running the extractor picks the fix up here.

## Running: Unicode in, ATASCII out

The Atari only understands ATASCII, and only knows the `.LST` extension. So
`Run` mirrors `helper/AtariBASIC-run.sh`: the current listing is converted with
`atascii.py -s -u` (`-s` strips host-only comments) and staged onto the
configured H: drive as an 8.3 `.LST` before the emulator is launched.

This is what makes the Unicode workflow honest -- you edit readable `.ULST`,
and the Atari receives true ATASCII with `$9B` line terminators. Requires
`ataricode800.hardDrivePath`; running a `.ULST` without it warns rather than
silently feeding Unicode to the emulator.

## Roadmap

- Full browser-based atari800 emulator (8bitworkshop-style) in a WebView.
- **BASIC-level debugger.** Not a novelty: the ROM already calls `TSTBRK` at
  the top of every statement (`EXECNS`), so a breakpoint is one byte
  (`BRKBYT = 0`) and the interpreter stops itself through its own supported
  path. Current line comes from `STMCUR` ($8A), watches from the VVT (which
  the Inspector already parses), break-on-error from `ERRNUM` ($B9). Design
  and honest limits: `~/wiki/atari-basic/basic-debugger-design.md`.
- Graphics tooling WebViews: charsets, player-missile graphics, tilemaps,
  display lists, sound/music, bitmap graphics.
- Possible partial JS port of the tokenizer for an interactive editor. Note
  the Sublime plugin constrains this: Python stays the source of truth for
  tokenizing, so a JS port would be *additive* (live editing/preview), not a
  replacement.
- Keep the Sublime plugin in tandem (shared tokenizer; grammar/theme
  converters keep the two editors' syntax in sync).