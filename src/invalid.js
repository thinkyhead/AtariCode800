/**
 * Background highlight for invalid.* scopes in Atari BASIC.
 *
 * VSCode themes cannot set a token BACKGROUND (tokenColors honour only
 * foreground and fontStyle), and extensions cannot query the scopes VSCode
 * assigned. So tokenize the document here with the same grammar and engine
 * (vscode-textmate + vscode-oniguruma) and paint invalid.warning /
 * invalid.error ranges with text decorations. Works in any theme.
 */

'use strict';

const vscode = require('vscode');
const fs = require('fs');
const path = require('path');

const LANGS = {
  ataribasic: { scope: 'source.ataribasic', file: 'ataribasic.tmLanguage.json' },
  'ataribasic-ci': { scope: 'source.ataribasic.ci', file: 'ataribasic-ci.tmLanguage.json' },
};
const DELAY_MS = 250;

let registryPromise = null;
let vt = null;
const grammars = {};
const timers = new Map();
let errorDeco = null;
let warnDeco = null;

function enabled() {
  return vscode.workspace.getConfiguration('ataricode800').get('highlightInvalid', true);
}

function makeDecorations() {
  // Same colours as the bundled themes (dark / light variants).
  errorDeco = vscode.window.createTextEditorDecorationType({
    dark: { backgroundColor: '#700005' },
    light: { backgroundColor: '#a00008', color: '#ffffff' },
  });
  warnDeco = vscode.window.createTextEditorDecorationType({
    dark: { backgroundColor: '#6b4317' },
    light: { backgroundColor: '#996022', color: '#ffffff' },
  });
}

function getRegistry(context) {
  if (registryPromise) return registryPromise;
  registryPromise = (async () => {
    vt = require('vscode-textmate');
    const onig = require('vscode-oniguruma');
    const wasm = fs.readFileSync(
      path.join(path.dirname(require.resolve('vscode-oniguruma')), 'onig.wasm'));
    await onig.loadWASM(wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength));
    const byScope = {};
    for (const l of Object.values(LANGS)) byScope[l.scope] = l.file;
    return new vt.Registry({
      onigLib: Promise.resolve({
        createOnigScanner: (p) => new onig.OnigScanner(p),
        createOnigString: (s) => new onig.OnigString(s),
      }),
      loadGrammar: async (scope) => {
        const f = byScope[scope];
        if (!f) return null;
        const p = path.join(context.extensionPath, 'syntax', f);
        return vt.parseRawGrammar(fs.readFileSync(p, 'utf8'), p);
      },
    });
  })();
  return registryPromise;
}

async function getGrammar(context, languageId) {
  const l = LANGS[languageId];
  if (!l) return null;
  if (!grammars[languageId]) {
    const reg = await getRegistry(context);
    grammars[languageId] = reg.loadGrammar(l.scope);
  }
  return grammars[languageId];
}

/** Collect invalid ranges, merging adjacent tokens of the same kind. */
function scan(grammar, doc, vtLib) {
  const errors = [];
  const warnings = [];
  let state = (vtLib || vt).INITIAL;
  for (let i = 0; i < doc.lineCount; i++) {
    const text = doc.lineAt(i).text;
    const r = grammar.tokenizeLine(text, state);
    state = r.ruleStack;
    let run = null;
    const flush = () => {
      if (!run) return;
      (run.kind === 'e' ? errors : warnings).push(
        new vscode.Range(i, run.start, i, run.end));
      run = null;
    };
    for (const t of r.tokens) {
      let kind = null;
      // Innermost invalid scope wins.
      for (let k = t.scopes.length - 1; k >= 0; k--) {
        const s = t.scopes[k];
        if (s.startsWith('invalid.warning')) { kind = 'w'; break; }
        if (s.startsWith('invalid')) { kind = 'e'; break; }
      }
      const end = Math.min(t.endIndex, text.length);
      if (kind && run && run.kind === kind && run.end === t.startIndex) {
        run.end = end;
      } else {
        flush();
        if (kind && end > t.startIndex) run = { kind, start: t.startIndex, end };
      }
    }
    flush();
  }
  return { errors, warnings };
}

async function update(context, editor) {
  if (!editor || !LANGS[editor.document.languageId]) return;
  if (!enabled()) {
    editor.setDecorations(errorDeco, []);
    editor.setDecorations(warnDeco, []);
    return;
  }
  try {
    const grammar = await getGrammar(context, editor.document.languageId);
    if (!grammar) return;
    const { errors, warnings } = scan(grammar, editor.document);
    editor.setDecorations(errorDeco, errors);
    editor.setDecorations(warnDeco, warnings);
  } catch (e) {
    console.error('AtariCode800 invalid highlight:', e);
  }
}

function schedule(context, doc) {
  for (const ed of vscode.window.visibleTextEditors) {
    if (ed.document !== doc) continue;
    const key = doc.uri.toString();
    clearTimeout(timers.get(key));
    timers.set(key, setTimeout(() => { timers.delete(key); update(context, ed); }, DELAY_MS));
  }
}

function register(context) {
  makeDecorations();
  const sub = context.subscriptions;
  sub.push(errorDeco, warnDeco);
  const all = () => vscode.window.visibleTextEditors.forEach((ed) => update(context, ed));
  sub.push(vscode.window.onDidChangeVisibleTextEditors(all));
  sub.push(vscode.workspace.onDidChangeTextDocument((e) => schedule(context, e.document)));
  sub.push(vscode.workspace.onDidChangeConfiguration((e) => {
    if (e.affectsConfiguration('ataricode800.highlightInvalid')) all();
  }));
  all();
}

module.exports = { register, scan };
