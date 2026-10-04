// Exercise src/invalid.js scan() outside VSCode with a stub 'vscode' module.
'use strict';
const Module = require('module');
const path = require('path');
class Range { constructor(a, b, c, d) { Object.assign(this, { l: a, s: b, e: d }); } }
const origLoad = Module._load;
Module._load = function (req, ...rest) {
  if (req === 'vscode') return { Range, window: {}, workspace: {} };
  return origLoad.call(this, req, ...rest);
};
const fs = require('fs');
const vt = require('vscode-textmate');
const onig = require('vscode-oniguruma');
const ROOT = path.join(__dirname, '..');
(async () => {
  const wasm = fs.readFileSync(path.join(path.dirname(require.resolve('vscode-oniguruma')), 'onig.wasm'));
  await onig.loadWASM(wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength));
  const reg = new vt.Registry({
    onigLib: Promise.resolve({ createOnigScanner: (p) => new onig.OnigScanner(p), createOnigString: (s) => new onig.OnigString(s) }),
    loadGrammar: async () => { const p = path.join(ROOT, 'syntax/ataribasic.tmLanguage.json'); return vt.parseRawGrammar(fs.readFileSync(p, 'utf8'), p); },
  });
  const g = await reg.loadGrammar('source.ataribasic');
  // scan() uses the module-level vt, which is set lazily; inject it.
  const inv = require(path.join(ROOT, 'src/invalid.js'));
  const lines = ['10 ?1:', '20 BYE:?"X"', '30 A=PEEK(3)*2', '40 POKE 10 :?'];
  const doc = { lineCount: lines.length, lineAt: (i) => ({ text: lines[i] }) };
  const r = inv.scan(g, doc, vt);
  const show = (k, a) => a.forEach((x) => console.log(k, JSON.stringify(lines[x.l].slice(x.s, x.e)), 'line', x.l + 1));
  show('error  ', r.errors); show('warning', r.warnings);
  const ok = r.errors.length === 2 && r.warnings.length === 1;
  console.log(ok ? 'invalid.js OK' : 'invalid.js FAIL'); process.exit(ok ? 0 : 1);
})();
