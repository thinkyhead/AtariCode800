// Probe TextMate/Oniguruma mechanics needed to emulate Sublime's `set:`.
//   node tools/probe_anchor.js
// Checks: \K keeps leading blanks out of a token; (?!\G) collapses a state
// region once its child returns; captures with `patterns` re-tokenise a span;
// (?=\n) does not fire at the end of a capture substring; \g<N> recursion
// matches balanced parens.
const vt = require('vscode-textmate'), oni = require('vscode-oniguruma'), fs = require('fs');

const STATE_END = (first) => `(?!\\G)|\\G(?=\\s*\\n)|\\G(?!\\s*(?:${first}))`;
const BAL = '(?<bal>(?:[^()"\\n]|"[^"\\n]*"|\\(\\g<bal>\\))*)';

const g = {
  scopeName: 'source.t',
  patterns: [{ begin: 'P', beginCaptures: { 0: { name: 'kw.p' } }, end: '(?=\\n)',
               patterns: [{ include: '#loop' }] }],
  repository: {
    // A LOOP context (like cmd_print): unanchored, ';' handled here.
    loop: { patterns: [
      { match: ';', name: 'delim.semi' },
      { include: '#expr_entry' },
    ] },
    // Entering the expression machine from a loop: first tokens, unanchored.
    expr_entry: { patterns: [
      { begin: '\\d+', name: 'num', end: STATE_END('[-+]'), patterns: [{ include: '#wants_op' }] },
      { begin: '[A-Z]+', beginCaptures: { 0: { name: 'var' } }, end: STATE_END('[-+]|\\('), patterns: [{ include: '#wants_op' }, { include: '#subscript' }] },
      { begin: '\\((?=' + BAL + '\\))', beginCaptures: { 0: { name: 'paren.open' } }, end: '(?=\\n)', patterns: [] },
    ] },
    subscript: { patterns: [
      { begin: '\\G\\s*\\K(\\()' + BAL + '(\\))',
        beginCaptures: { 1: { name: 'paren.open' }, 2: { patterns: [{ include: '#loop' }] }, 3: { name: 'paren.close' } },
        end: STATE_END('[-+]'), patterns: [{ include: '#wants_op' }] },
    ] },
    wants_op: { patterns: [
      { begin: '\\G\\s*\\K([-+])', beginCaptures: { 1: { name: 'op' } }, end: STATE_END('\\d+|[A-Z]+'),
        patterns: [
          { begin: '\\G\\s*\\K\\d+', name: 'num', end: STATE_END('[-+]'), patterns: [{ include: '#wants_op' }] },
          { begin: '\\G\\s*\\K[A-Z]+', beginCaptures: { 0: { name: 'var' } }, end: STATE_END('[-+]|\\('), patterns: [{ include: '#wants_op' }, { include: '#subscript' }] },
          { match: '\\G\\s*\\K(?=\\S)(?!\\n).*', name: 'invalid' } ] },
      { match: '\\G\\s*\\K(\\d+|[A-Z]+).*', name: 'invalid' },
    ] },
  },
};

oni.loadWASM(fs.readFileSync(require.resolve('vscode-oniguruma/release/onig.wasm')).buffer).then(async () => {
  const reg = new vt.Registry({
    onigLib: Promise.resolve({ createOnigScanner: s => new oni.OnigScanner(s), createOnigString: s => new oni.OnigString(s) }),
    loadGrammar: async () => g });
  const gr = await reg.loadGrammar('source.t');
  let state = vt.INITIAL;
  for (const line of ['P A;B', 'P A + 1;B C', 'P 1 +', 'P A(1+B(2));C', 'P A(1;2) - X', 'P X']) {
    const r = gr.tokenizeLine(line, state);
    console.log('--- ' + line + (r.ruleStack.depth > 1 ? `   [OPEN depth=${r.ruleStack.depth}]` : ''));
    for (const t of r.tokens)
      console.log('  ' + JSON.stringify(line.slice(t.startIndex, t.endIndex)).padEnd(9) + t.scopes.slice(1).join(' '));
    state = r.ruleStack;
  }
});
