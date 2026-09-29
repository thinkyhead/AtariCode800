/**
 * AtariCode800 - BASIC Inspector WebView
 *
 * The four panes from the Tkinter prototype, in VSCode:
 *   1. the re-LISTed program (with a Compact checkbox)
 *   2. the tokenized hex, per line
 *   3. the VNT (variable names)
 *   4. the VVT (variable types)
 *
 * All data comes from `basic.py --json`, so the Inspector can never disagree
 * with the tokenizer.
 */

'use strict';

const vscode = require('vscode');
const path = require('path');
const basic = require('./basic');

let panel = null;
let currentFile = null;

function esc(s) {
  return String(s === undefined || s === null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

const TYPE_NAMES = {
  0x00: 'numeric',
  0x40: 'array',
  0x41: 'string array',
  0x80: 'string',
};

function render(data, fileName) {
  const compact = vscode.workspace
    .getConfiguration('ataricode800').get('compactListing', false);

  const hexRows = (data.lines || []).map((l) => {
    const bytes = (l.hex.match(/../g) || []).join(' ');
    return `<tr><td class="ln">${l.line}</td><td class="hex">${esc(bytes)}</td></tr>`;
  }).join('');

  const varRows = (data.vnt || []).map((name, i) => {
    const v = (data.vvt || [])[i] || {};
    const type = TYPE_NAMES[v.type] || `0x${Number(v.type || 0).toString(16)}`;
    const tok = `$${(0x80 | i).toString(16).toUpperCase()}`;
    const extra = v.declared_only ? '<span class="muted">declared</span>' :
      (v.value !== undefined && v.value !== null ? esc(v.value) : '');
    return `<tr><td class="tok">${tok}</td><td class="vname">${esc(name)}</td>` +
           `<td>${esc(type)}</td><td>${extra}</td></tr>`;
  }).join('');

  const err = data.error
    ? `<div class="error">${esc(data.error)}</div>` : '';

  return `<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body { font-family: var(--vscode-editor-font-family, monospace);
         font-size: var(--vscode-editor-font-size, 13px);
         color: var(--vscode-editor-foreground);
         background: var(--vscode-editor-background); padding: 0 12px 24px; }
  h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .08em;
       opacity: .75; margin: 18px 0 6px; border-bottom: 1px solid
       var(--vscode-panel-border, #444); padding-bottom: 4px; }
  pre { margin: 0; white-space: pre-wrap; word-break: break-word; }
  table { border-collapse: collapse; width: 100%; }
  td, th { padding: 2px 8px 2px 0; vertical-align: top; text-align: left; }
  .ln { text-align: right; opacity: .65; width: 5em; }
  .hex { font-family: monospace; word-break: break-all; }
  .tok { opacity: .65; width: 4em; }
  .vname { font-weight: 600; }
  .muted { opacity: .55; font-style: italic; }
  .bar { position: sticky; top: 0; padding: 10px 0 6px;
         background: var(--vscode-editor-background); }
  .file { opacity: .6; font-size: 11px; }
  .error { color: var(--vscode-errorForeground, #f14c4c);
           border: 1px solid currentColor; padding: 6px 8px; margin: 10px 0; }
  label { user-select: none; cursor: pointer; }
</style></head>
<body>
  <div class="bar">
    <label><input type="checkbox" id="compact" ${compact ? 'checked' : ''}>
      Compact listing</label>
    <div class="file">${esc(fileName)}</div>
  </div>
  ${err}

  <h2>Listing</h2>
  <pre id="listing">${esc(compact ? data.listing_abbr : data.listing)}</pre>

  <h2>Tokenized (hex)</h2>
  <div class="file">header ${esc(data.header || '')}</div>
  <table>${hexRows}</table>

  <h2>Variables (VNT / VVT)</h2>
  <table>
    <tr><th>Tok</th><th>Name</th><th>Type</th><th>Value</th></tr>
    ${varRows || '<tr><td colspan="4" class="muted">No variables</td></tr>'}
  </table>

<script>
  const vscodeApi = acquireVsCodeApi();
  const full = ${JSON.stringify(data.listing || '')};
  const abbr = ${JSON.stringify(data.listing_abbr || '')};
  const box = document.getElementById('compact');
  box.addEventListener('change', () => {
    document.getElementById('listing').textContent = box.checked ? abbr : full;
    vscodeApi.postMessage({ type: 'compact', value: box.checked });
  });
</script>
</body></html>`;
}

async function update(context, doc) {
  if (!panel) return;
  try {
    const data = await basic.inspectFile(context, doc.fileName);
    currentFile = doc.fileName;
    panel.webview.html = render(data, path.basename(doc.fileName));
  } catch (e) {
    panel.webview.html =
      `<body style="font-family:monospace;padding:16px">
         <h3>AtariCode800 Inspector</h3>
         <pre style="color:#f14c4c;white-space:pre-wrap">${esc(e.message)}</pre>
       </body>`;
  }
}

async function open(context) {
  const ed = vscode.window.activeTextEditor;
  if (!ed || ed.document.languageId !== 'ataribasic') {
    vscode.window.showErrorMessage(
      'AtariCode800: open an Atari BASIC .LST/.ULST file first.');
    return;
  }
  if (ed.document.isDirty) await ed.document.save();

  if (!panel) {
    panel = vscode.window.createWebviewPanel(
      'ataricode800.inspector', 'BASIC Inspector',
      vscode.ViewColumn.Beside, { enableScripts: true, retainContextWhenHidden: true });

    panel.onDidDispose(() => { panel = null; currentFile = null; },
      null, context.subscriptions);

    panel.webview.onDidReceiveMessage((msg) => {
      if (msg && msg.type === 'compact') {
        vscode.workspace.getConfiguration('ataricode800')
          .update('compactListing', msg.value, true);
      }
    }, null, context.subscriptions);
  }
  panel.reveal(vscode.ViewColumn.Beside, true);
  await update(context, ed.document);
}

function refreshIfOpen(context, doc) {
  if (panel && (!currentFile || currentFile === doc.fileName)) {
    update(context, doc);
  }
}

module.exports = { open, refreshIfOpen };
