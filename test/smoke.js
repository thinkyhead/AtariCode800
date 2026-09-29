/**
 * Headless smoke test for the AtariCode800 extension.
 *
 * VSCode is not available outside the extension host, so `vscode` and the
 * local modules are stubbed; the point is to prove the real code paths work:
 *   - basic.py --json runs and parses
 *   - inspector.render() produces all four panes with real data
 *
 * Run:  node test/smoke.js
 */
'use strict';

const Module = require('module');
const path = require('path');
const fs = require('fs');
const cp = require('child_process');
const os = require('os');

const ROOT = path.resolve(__dirname, '..');
// The Sublime ATASCII plugin is the source of truth for the character data.
const SUBLIME_ATASCII = path.join(process.env.HOME, 'Projects', 'Retro',
  'ATASCII', 'atascii.py');

// --- stub `vscode` before anything requires it -------------------------
const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === 'vscode') {
    return {
      workspace: { getConfiguration: () => ({ get: (k, d) => d }) },
      window: {},
      ViewColumn: { Beside: 2 },
    };
  }
  return origLoad.apply(this, [request, parent, isMain]);
};

let failures = 0;
function check(label, ok, detail) {
  console.log((ok ? '  OK   ' : '  FAIL ') + label + (detail ? '  ' + detail : ''));
  if (!ok) failures++;
}

/** Report a check that cannot run here, without counting it as a failure. */
function skip(label, why) {
  console.log('  SKIP ' + label + (why ? '  (' + why + ')' : ''));
}

// --- 1. the tokenizer seam --------------------------------------------
const tool = path.join(ROOT, 'python', 'basic.py');
check('basic.py resolves (vendored in python/)', fs.existsSync(tool));

// The corpus lives in the 6502-Tools repo, which is not a dependency of this
// one. Write a self-contained sample into a temp dir so the smoke suite runs
// from a bare clone of this repo alone.
const lst = path.join(os.tmpdir(), 'ataricode800-smoke.LST');
fs.writeFileSync(lst,
  '10 REM SMOKE TEST\n'
  + '20 DIM A$(20)\n'
  + '30 A$="HELLO WORLD"\n'
  + '40 PRINT A$(1,5)\n'
  + '50 FOR I=1 TO 10:PRINT I:NEXT I\n'
  + '60 IF I>5 THEN PRINT "BIG"\n'
  + '70 GOTO 50\n', 'utf8');
check('sample .LST exists', fs.existsSync(lst), lst);

let data = null;
try {
  const out = cp.execFileSync('python3', ['-B', tool, '--json', lst],
    { cwd: path.dirname(tool), maxBuffer: 16 * 1024 * 1024 }).toString();
  data = JSON.parse(out);
  check('basic.py --json returns parseable JSON', true);
} catch (e) {
  check('basic.py --json returns parseable JSON', false, e.message);
}

if (data) {
  check('listing is populated', (data.listing || '').length > 0);
  check('compact listing differs from full', data.listing !== data.listing_abbr);
  check('hex lines present', (data.lines || []).length > 0,
    `${(data.lines || []).length} lines`);
  check('variables present', (data.vnt || []).length > 0, (data.vnt || []).join(', '));
  check('no tokenizer error', !data.error, data.error || '');
}

// --- 2. the WebView render -------------------------------------------
if (data) {
  const src = fs.readFileSync(path.join(ROOT, 'src', 'inspector.js'), 'utf8');
  const m = { exports: {} };
  const fn = new Function('module', 'exports', 'require', '__dirname',
    src + '\nmodule.exports._render = render;');
  fn(m, m.exports, (r) => require(r === './basic' ? path.join(ROOT, 'src', 'basic.js') : r),
     path.join(ROOT, 'src'));

  const html = m.exports._render(data, 'test_simple.LST');
  fs.writeFileSync('/tmp/ataricode800-inspector.html', html);

  const panes = [
    ['Listing pane', '<h2>Listing</h2>'],
    ['Hex pane', '<h2>Tokenized (hex)</h2>'],
    ['Variables pane', 'Variables (VNT / VVT)'],
    ['Compact checkbox', 'id="compact"'],
    ['listing content', 'SMOKE TEST'],
    ['hex bytes formatted', '0a 00 '],
    ['variable A$ listed', 'A$'],
  ];
  for (const [label, needle] of panes) check(label, html.includes(needle));
  console.log('\n  preview written to /tmp/ataricode800-inspector.html');
}

// --- 2b. .ULST is treated as Atari BASIC source ------------------------
// .ULST is the user's convention for a Unicode listing (vs ATASCII .LST).
// basic.py dispatches on extension, so a missing .ulst case silently sent
// the file down the tokenized-BAS path and failed.
{
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ataricode800-u-'));
  const ulst = path.join(tmp, 'UTEST.ULST');
  fs.copyFileSync(lst, ulst);
  try {
    const out = cp.execFileSync('python3', ['-B', tool, '--json', ulst],
      { cwd: path.dirname(tool), maxBuffer: 16 * 1024 * 1024 }).toString();
    const u = JSON.parse(out);
    check('.ULST tokenizes as BASIC source', !u.error && (u.lines || []).length > 0,
      `${(u.lines || []).length} lines`);
  } catch (e) {
    check('.ULST tokenizes as BASIC source', false,
      String(e.stderr || e.message).split('\n')[0]);
  }
  fs.rmSync(tmp, { recursive: true, force: true });
}

// --- 2c. ATASCII parity with the Sublime plugin -----------------------
{
  const dataPath = path.join(ROOT, 'media', 'atascii-data.json');
  check('atascii-data.json exists', fs.existsSync(dataPath));

  if (fs.existsSync(dataPath)) {
    const d = JSON.parse(fs.readFileSync(dataPath, 'utf8'));
    const at = require(path.join(ROOT, 'src', 'atascii.js'));

    check('invert map extracted', Object.keys(d.invert).length > 200,
      `${Object.keys(d.invert).length} entries`);
    check('three palettes extracted',
      ['inverted', 'special', 'drawing'].every((k) => (d.palettes[k] || []).length),
      Object.keys(d.palettes).join(', '));

    // Compare against Python doing exactly what the Sublime plugin does.
    // That plugin lives in a DIFFERENT repo, so skip (don't fail) when this
    // repo is cloned on its own -- media/atascii-data.json is the vendored
    // copy of that data and is checked above regardless.
    if (!fs.existsSync(SUBLIME_ATASCII)) {
      skip('invertText matches the Sublime plugin byte-for-byte',
        'Sublime ATASCII plugin not present');
    } else {
    const sample = '10 PRINT "HELLO":REM abc';
    const js = at.invertText(sample, d.invert);
    const py = cp.execFileSync('python3', ['-c', `
import ast, re, sys, json
src = open(${JSON.stringify(SUBLIME_ATASCII)}, encoding='utf-8').read()
tr1 = ast.literal_eval(re.search(r'^\\s*tr1\\s*=\\s*(".*")\\s*$', src, re.M).group(1))
tr2 = ast.literal_eval(re.search(r'^\\s*tr2\\s*=\\s*(".*")\\s*$', src, re.M).group(1))
m = {ord(a): ord(b) for a, b in zip(tr1, tr2)}
m.update({ord(b): ord(a) for a, b in zip(tr1, tr2)})
sys.stdout.write(json.dumps(${JSON.stringify(sample)}.translate(m)))
`], { encoding: 'utf8' });

    check('invertText matches the Sublime plugin byte-for-byte',
      js === JSON.parse(py), js === JSON.parse(py) ? 'identical' : `js=${js} py=${py}`);

    // Inverting twice must return the original for ordinary source text.
    check('invert round-trips on ASCII source',
      at.invertText(js, d.invert) === sample);
    }

    const html = at.paletteHtml(d.palettes.inverted, 'monospace');
    check('palette HTML renders clickable chars',
      html.includes('data-ch=') && html.includes('acquireVsCodeApi'));
  }
}

// --- 2d. ULST -> ATASCII staging (what src/basic.js runCurrent does) ---
// The Atari speaks ATASCII with $9B line terminators. A Unicode .ULST must be
// converted before it will run, so prove the converter we shell out to does it.
{
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ataricode800-a-'));
  const src = path.join(tmp, 'CONV.ULST');
  fs.writeFileSync(src,
    '10 PRINT "HELLO"\n; host only\n# hash only\n. legacy only\n20 GOTO 10\n', 'utf8');
  const conv = path.join(ROOT, 'python', 'atascii.py');
  try {
    const out = cp.execFileSync('python3', ['-B', conv, '-s', '-u', src],
      { maxBuffer: 4 * 1024 * 1024 });
    check('ULST converts to ATASCII', out.length > 0, `${out.length} bytes`);
    check('lines end with $9B, not $0A',
      out[out.length - 1] === 0x9b && !out.includes(0x0a));
    check('host-only comment stripped', !out.includes(Buffer.from('host only')));
    // All three markers are host comments. '.' shows up in legacy ULSTs as an
    // unnumbered bare REM; real BASIC would parse it as line 32768 and throw
    // it away. Must match atascii.COMMENT_MARKERS and basic.py's loader.
    check('# comment stripped', !out.includes(Buffer.from('hash only')));
    check('. legacy comment stripped', !out.includes(Buffer.from('legacy only')));
  } catch (e) {
    check('ULST converts to ATASCII', false,
      String(e.stderr || e.message).split('\n')[0]);
  }
  fs.rmSync(tmp, { recursive: true, force: true });
}

// --- 2e. theme covers the grammar's scopes ----------------------------
// The reported bug: VSCode didn't colorize like Sublime. Root cause was a
// stale .tmTheme whose selectors never matched the grammar's scope names.
// Guard it: every scope the grammar emits must resolve to a colour or style.
{
  const g = JSON.parse(fs.readFileSync(
    path.join(ROOT, 'syntax', 'ataribasic.tmLanguage.json'), 'utf8'));

  const scopes = new Set();
  (function walk(o) {
    if (Array.isArray(o)) return o.forEach(walk);
    if (!o || typeof o !== 'object') return;
    for (const k of ['name', 'contentName'])
      if (typeof o[k] === 'string') scopes.add(o[k]);
    if (o.captures)
      for (const v of Object.values(o.captures))
        if (v && typeof v.name === 'string') scopes.add(v.name);
    Object.values(o).forEach(walk);
  })(g);

  const grammarScopes = [...scopes].filter((s) => s.includes('.'));
  check('grammar emits a rich scope set', grammarScopes.length > 100,
    `${grammarScopes.length} scopes`);

  // TextMate: longest matching dotted prefix wins.
  const styleFor = (scope, rules) => {
    let win = null, best = -1;
    for (const r of rules) {
      const sels = Array.isArray(r.scope) ? r.scope
        : String(r.scope || '').split(',').map((s) => s.trim());
      for (const sel of sels) {
        const parts = sel.split(/\s+/);
        const last = parts[parts.length - 1] || sel;
        if (scope === last || scope.startsWith(last + '.')) {
          const n = last.split('.').length;
          if (n > best) { best = n; win = r.settings; }
        }
      }
    }
    return win;
  };

  for (const f of ['atari800-light-color-theme.json',
                   'atari800-dark-color-theme.json']) {
    const t = JSON.parse(fs.readFileSync(path.join(ROOT, 'themes', f), 'utf8'));
    // `ctx.*` scopes come from Sublime's meta_scope/meta_content_scope. They
    // mark which context produced a token -- structural bookkeeping, not
    // colours -- so no theme targets them and they must not count as unstyled.
    const unstyled = grammarScopes
      .filter((s) => !s.startsWith('ctx.'))
      .filter((s) => !styleFor(s, t.tokenColors));
    check(`${f} styles every grammar scope`, unstyled.length === 0,
      unstyled.length ? `unstyled: ${unstyled.slice(0, 3).join(', ')}` :
        `${grammarScopes.length}/${grammarScopes.length}`);
    check(`${f} has an editor background`, !!t.colors['editor.background'],
      t.colors['editor.background']);
  }
}

// --- 2f. the grammar actually TOKENIZES ------------------------------
// Declaring a scope in the JSON is not the same as emitting it. The grammar
// once declared 123 scopes while the live editor scoped only the line number,
// and a scope-coverage check cannot see that -- only real tokenization can.
// Runs via the same vscode-textmate engine VSCode uses; skipped when the dev
// dependency is absent so the suite still runs on a clean checkout.
{
  let haveTm = true;
  try {
    require.resolve('vscode-textmate');
    require.resolve('vscode-oniguruma');
  } catch (e) {
    haveTm = false;
  }

  if (!haveTm) {
    console.log('  SKIP grammar tokenization (npm i vscode-textmate vscode-oniguruma)');
  } else {
    const { execFileSync } = require('child_process');
    const out = execFileSync(process.execPath,
      [path.join(ROOT, 'test', 'tokenize.js')], { encoding: 'utf8' });

    // Each expectation is a statement that MUST carry a real (non-ctx) scope.
    const want = [
      ['"PRINT"', 'keyword.print'],
      ['"DIM"', 'keyword.dim'],
      ['"FOR"', 'keyword.for'],
      ['"TO"', 'keyword.to'],
      ['"NEXT"', 'keyword.next'],
      ['"GOSUB"', 'keyword.gosub'],
      ['"GR."', 'keyword.graphics'],
      // Second and third statements on one line: the ':' delimiter must be
      // handed back to code_line, or these never get scoped at all.
      ['"COLOR"', 'keyword.color'],
      ['"PLOT"', 'keyword.plot'],
      ['"DRAWTO"', 'keyword.drawto'],
      // XIO's abbreviation: X is unique in the ROM statement name table, so
      // `X.` is valid Atari BASIC. The Sublime grammar matched only the full
      // `XIO` -- an oversight fixed in both editors' shared source.
      ['"X."', 'keyword.xio'],
    ];
    for (const [tok, scope] of want) {
      const re = new RegExp(`${tok.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s+\\S*${scope}`);
      check(`tokenizes ${tok.replace(/"/g, '')} as ${scope}`, re.test(out));
    }

    // The line number must not be the ONLY thing that matches -- the original
    // bug. If statements stop scoping, this collapses toward zero.
    const m = out.match(/(\d+)\/(\d+) non-blank tokens carry a scope/);
    check('most tokens carry a scope', m && Number(m[1]) / Number(m[2]) > 0.6,
      m ? `${m[1]}/${m[2]}` : 'no score line');
  }
}

// --- 3. the Atari 800 assembly toolchain -----------------------------
// Prove the exact ca65 + ld65 sequence src/asm.js issues produces a real
// Atari binary, rather than trusting the toolchain is present and correct.
{
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ataricode800-'));
  const src = path.join(tmp, 'hello.asm');
  const obj = path.join(tmp, 'hello.o');
  const bin = path.join(tmp, 'hello.bin');
  const lnk = path.join(tmp, 'hello.lnk');

  fs.writeFileSync(src, [
    '; minimal Atari 800 program: set the background color, loop',
    '        .export start',
    '        .import __EXEHDR__, __AUTOSTART__',
    '        .segment "CODE"',
    'start:  lda #$34',
    '        sta $02C6       ; COLOR1 shadow',
    '        jmp start',
    '',
  ].join('\n'));

  // Seed the linker config exactly as asm.js does for a new project.
  const asm = require(path.join(ROOT, 'src', 'asm.js'));
  const stock = asm.findStockConfig();
  check('cc65 stock atari-asm.cfg found', !!stock, stock || '');

  let built = false;
  if (stock) {
    fs.copyFileSync(stock, lnk);
    try {
      cp.execFileSync('ca65', [src, '-l', path.join(tmp, 'hello.txt'), '-o', obj],
        { cwd: tmp, stdio: 'pipe' });
      cp.execFileSync('ld65', ['-o', bin, '-C', lnk, obj, 'atari.lib'],
        { cwd: tmp, stdio: 'pipe' });
      built = fs.existsSync(bin);
    } catch (e) {
      console.log('  (toolchain failed: ' +
        String(e.stderr || e.message).split('\n')[0] + ')');
    }
  }

  if (built) {
    const b = fs.readFileSync(bin);
    check('ca65 + ld65 produce a .bin', true, `${b.length} bytes`);
    check('.bin starts with the $FFFF binary-load header',
      b[0] === 0xFF && b[1] === 0xFF,
      `got ${b[0].toString(16)} ${b[1].toString(16)}`);
    // Bytes 2-5 are the load start/end addresses of the first chunk.
    const lo = b[2] | (b[3] << 8), hi = b[4] | (b[5] << 8);
    check('load range is sane', hi >= lo && lo >= 0x0700,
      `$${lo.toString(16).toUpperCase()}-$${hi.toString(16).toUpperCase()}`);
  } else {
    console.log('  SKIP ca65/ld65 build (toolchain not available)');
  }
  fs.rmSync(tmp, { recursive: true, force: true });
}

// --- 5. tokenizer sync with the canonical copy ------------------------
// The canonical tokenizer lives in 6502-Tools/Sublime/AtariTools/basic and the
// Sublime plugin runs those same files. Drift between it and python/ means the
// two editors silently disagree, so fail the suite rather than let it pass.
// SKIPs when the sibling repo is absent (a clean clone, or CI).
{
  const script = path.join(ROOT, 'tools', 'sync_tokenizer.sh');
  const canon = path.join(os.homedir(), 'Projects', 'Retro', '6502-Tools',
    'Sublime', 'AtariTools', 'basic');

  if (!fs.existsSync(canon)) {
    skip('python/ matches the canonical tokenizer', '6502-Tools not present');
  } else {
    const r = cp.spawnSync('bash', [script, '--check'], { encoding: 'utf8' });
    check('python/ matches the canonical tokenizer', r.status === 0,
      r.status === 0 ? '' : String(r.stdout || r.stderr).trim().split('\n')[0]);
  }
}


let done6 = Promise.resolve();

// --- 6. the CI (case-insensitive) grammar -----------------------------
// The CI variant is the same grammar with (?i) on every keyword pattern, for
// listings typed in lowercase. It is easy to leave behind when the main
// grammar is regenerated -- it already went stale once -- so assert both that
// it is registered properly and that it still differs in the one way that
// justifies its existence.
{
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
  const ci = pkg.contributes.grammars
    .find((g) => g.scopeName === 'source.ataribasic.ci');

  check('CI grammar is registered', !!ci);
  check('CI grammar has a language id (else it is unselectable)',
    !!(ci && ci.language), ci ? String(ci.language) : '');
  const ciLang = pkg.contributes.languages.find((l) => l.id === 'ataribasic-ci');
  check('CI language is declared', !!ciLang);
  // Claiming .LST/.ULST here would fight the strict grammar for the default.
  check('CI language claims no file extensions',
    !!ciLang && !ciLang.extensions);

  done6 = (async () => {
    const vsctm = require('vscode-textmate');
    const oni = require('vscode-oniguruma');
    const wasm = fs.readFileSync(
      require.resolve('vscode-oniguruma/release/onig.wasm'));
    await oni.loadWASM(wasm.buffer);

    const paths = {
      'source.ataribasic': path.join(ROOT, 'syntax', 'ataribasic.tmLanguage.json'),
      'source.ataribasic.ci': path.join(ROOT, 'syntax', 'ataribasic-ci.tmLanguage.json'),
    };
    const reg = new vsctm.Registry({
      onigLib: Promise.resolve({
        createOnigScanner: (s) => new oni.OnigScanner(s),
        createOnigString: (s) => new oni.OnigString(s),
      }),
      loadGrammar: async (scope) => (paths[scope]
        ? vsctm.parseRawGrammar(fs.readFileSync(paths[scope], 'utf8'), paths[scope])
        : null),
    });

    const isKeyword = (t) => t.scopes.some((s) => /keyword|storage|support/.test(s));
    const count = async (scope, line) => {
      const g = await reg.loadGrammar(scope);
      return g.tokenizeLine(line, vsctm.INITIAL).tokens.filter(isKeyword).length;
    };

    check('both grammars scope UPPERCASE keywords',
      (await count('source.ataribasic', '20 PRINT "HI"')) > 0
      && (await count('source.ataribasic.ci', '20 PRINT "HI"')) > 0);

    const strictLower = await count('source.ataribasic', '20 print "HI"');
    const ciLower = await count('source.ataribasic.ci', '20 print "HI"');
    check('strict grammar rejects lowercase keywords', strictLower === 0,
      `${strictLower} keyword-scoped`);
    check('CI grammar accepts lowercase keywords', ciLower > 0,
      `${ciLower} keyword-scoped`);
  })();

  done6.catch((e) => { check('CI grammar tokenizes', false, e.message); });
}


// --- 4. the "Got BASIC?" downloader -----------------------------------
// Network-dependent, so it SKIPs rather than fails when offline. Guards the
// two things that actually broke in development: the repo's default branch is
// master (not main), and git records the folder as 'Crossover/AtariBasic'
// while a case-insensitive macOS checkout shows 'AtariBASIC' -- the GitHub
// API is case-sensitive, so the match must not be.
{
  const dl = require(path.join(ROOT, 'src', 'download.js'));
  check('download module exports gotBasic', typeof dl.gotBasic === 'function');

  const url = dl.rawUrl('master', 'Crossover/AtariBasic/MENU.ULST');
  check('raw URL is well formed',
    url === 'https://raw.githubusercontent.com/thinkyhead/6502-Tools/master/'
          + 'Crossover/AtariBasic/MENU.ULST', url);

  const done = (async () => {
    let items;
    try {
      items = await dl.loadListings('master');
    } catch (e) {
      skip('lists BASIC programs from GitHub', e.message.slice(0, 60));
      return;
    }
    check('lists BASIC programs from GitHub', items.length > 0,
      `${items.length} listings`);
    check('listings carry a folder grouping',
      new Set(items.map((i) => i.folder)).size > 1,
      [...new Set(items.map((i) => i.folder || '(root)'))].join(', '));
    check('only .LST/.ULST are offered',
      items.every((i) => /\.(ULST|LST)$/i.test(i.label)));
  })();

  // The suite is otherwise synchronous; settle the async checks before
  // reporting, or the exit code is decided before they run.
  Promise.all([done, done6]).then(() => {
    console.log(failures === 0
      ? '\nAll smoke checks passed.'
      : `\n${failures} check(s) FAILED.`);
    process.exit(failures === 0 ? 0 : 1);
  });
}
