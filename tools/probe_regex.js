// Which regex constructs does vscode-oniguruma accept?  node tools/probe_regex.js
const oni = require('vscode-oniguruma'), fs = require('fs');
oni.loadWASM(fs.readFileSync(require.resolve('vscode-oniguruma/release/onig.wasm')).buffer).then(() => {
  const tests = {
    'K keep':       ['\\G\\s*\\K(\\d+)', '  12', 0],
    'rel recursion':['\\(((?:[^()]|\\((?-1)\\))*)\\)', 'x(a(b)c)', 0],
    'abs recursion':['\\(((?:[^()]|\\(\\g<1>\\))*)\\)', 'x(a(b)c)', 0],
    'lookahead \\n': ['a(?=\\n)', 'a\n', 0],
    'neg G':        ['(?!\\G)', 'abc', 0],
  };
  for (const [name, [rx, str, pos]] of Object.entries(tests)) {
    try {
      const sc = new oni.OnigScanner([rx]);
      const m = sc.findNextMatchSync(new oni.OnigString(str), pos);
      console.log(name.padEnd(15), m ? JSON.stringify(m.captureIndices.map(c => str.slice(c.start, c.end))) + ' @' + m.captureIndices[0].start : 'no match');
    } catch (e) { console.log(name.padEnd(15), 'ERROR', e.message); }
  }
});
