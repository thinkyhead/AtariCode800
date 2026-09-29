'use strict';

/**
 * Tokenize sample lines with the REAL TextMate engine (the same
 * vscode-textmate + vscode-oniguruma VSCode itself uses), so grammar work is
 * verified against actual tokenization instead of eyeballing a screenshot.
 *
 *   node test/tokenize.js                  # tokenize the built-in samples
 *   node test/tokenize.js '10 PRINT "HI"'  # tokenize one line
 *   node test/tokenize.js --file X.LST     # tokenize a file
 */

const fs = require('fs');
const path = require('path');
const vsctm = require('vscode-textmate');
const oniguruma = require('vscode-oniguruma');

const ROOT = path.resolve(__dirname, '..');
const GRAMMAR = path.join(ROOT, 'syntax', 'ataribasic.tmLanguage.json');

const SAMPLES = [
  '10 PRINT "HELLO"',
  '20 DIM A$(20)',
  '30 FOR I=1 TO 10',
  '40 IF X>5 THEN PRINT "BIG"',
  '50 GOSUB 1000',
  '60 REM this is a remark',
  '70 A=ABS(-5)+INT(3.7)',
  '80 POKE 710,14:SOUND 0,121,10,8',
  '90 ? #6;"HI"',
  '100 NEXT I',
  '110 GR. 8:COLOR 1:PLOT 0,0:DRAWTO 319,191',
  '120 X.18,#1,0,0,"S:"',
];

async function makeRegistry() {
  const wasm = fs.readFileSync(path.join(
    ROOT, 'node_modules', 'vscode-oniguruma', 'release', 'onig.wasm'));
  await oniguruma.loadWASM(wasm.buffer);

  return new vsctm.Registry({
    onigLib: Promise.resolve({
      createOnigScanner: (s) => new oniguruma.OnigScanner(s),
      createOnigString: (s) => new oniguruma.OnigString(s),
    }),
    loadGrammar: async (scopeName) => {
      if (scopeName !== 'source.ataribasic') return null;
      const raw = fs.readFileSync(GRAMMAR, 'utf8');
      return vsctm.parseRawGrammar(raw, GRAMMAR);
    },
  });
}

/** Scopes that carry actual highlighting.
 *
 * Sublime's `meta_scope`/`meta_content_scope` become `ctx.*` names. They are
 * structural bookkeeping, not colours -- no theme targets them. Counting them
 * as "scoped" would let a token look handled while rendering unstyled, so they
 * are excluded from the score.
 */
function interesting(scopes) {
  return scopes.filter((s) => s !== 'source.ataribasic' && !s.startsWith('ctx.'));
}

async function main() {
  const args = process.argv.slice(2);
  let lines = SAMPLES;
  if (args[0] === '--file') {
    lines = fs.readFileSync(args[1], 'utf8').split(/\r?\n/).filter(Boolean);
  } else if (args.length) {
    lines = [args.join(' ')];
  }

  const registry = await makeRegistry();
  const grammar = await registry.loadGrammar('source.ataribasic');
  if (!grammar) {
    console.error('FAILED to load grammar');
    process.exit(1);
  }

  let ruleState = vsctm.INITIAL;
  let unscoped = 0, total = 0;

  for (const line of lines) {
    const r = grammar.tokenizeLine(line, ruleState);
    ruleState = vsctm.INITIAL;          // each BASIC line is independent

    console.log(`\n${line}`);
    for (const t of r.tokens) {
      const text = line.substring(t.startIndex, t.endIndex);
      if (!text.trim()) continue;
      total++;
      const sc = interesting(t.scopes);
      if (!sc.length) unscoped++;
      console.log(`   ${JSON.stringify(text).padEnd(22)} ${
        sc.join(' ') || '\u001b[31m<UNSCOPED>\u001b[0m'}`);
    }
  }

  console.log(`\n${total - unscoped}/${total} non-blank tokens carry a scope`);
  if (unscoped) {
    console.log(`\u001b[31m${unscoped} token(s) fell through with no scope\u001b[0m`);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
