'use strict';

/**
 * ATASCII support, ported from the Sublime plugin at ~/Projects/Retro/ATASCII.
 *
 *   - Insert Inverted / Special / Drawing : pick a character from a palette
 *   - Invert Selected Text                : swap normal <-> inverse
 *
 * The character data is NOT retyped here. `tools/extract_atascii.py` reads it
 * out of the Sublime plugin into `media/atascii-data.json`, so the two editors
 * can never disagree about a codepoint. Re-run that script if the Sublime side
 * changes.
 */

const vscode = require('vscode');
const fs = require('fs');
const path = require('path');

let _data = null;

function data(context) {
  if (!_data) {
    const p = path.join(context.extensionPath, 'media', 'atascii-data.json');
    _data = JSON.parse(fs.readFileSync(p, 'utf8'));
  }
  return _data;
}

/** Swap normal and inverse characters, exactly as the Sublime plugin does. */
function invertText(text, map) {
  let out = '';
  for (const ch of text) out += (map[ch] !== undefined ? map[ch] : ch);
  return out;
}

async function invertSelection(context) {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showErrorMessage('ATASCII: no active editor.');
    return;
  }
  const map = data(context).invert;
  const edits = [];
  for (const sel of editor.selections) {
    if (sel.isEmpty) continue;
    const text = editor.document.getText(sel);
    const next = invertText(text, map);
    if (next !== text) edits.push([sel, next]);
  }
  if (!edits.length) {
    vscode.window.setStatusBarMessage('ATASCII: select some text to invert', 3000);
    return;
  }
  await editor.edit((b) => { for (const [sel, next] of edits) b.replace(sel, next); });
}

/** Build the palette WebView HTML, mirroring the Sublime popup's layout. */
function paletteHtml(rows, fontCss) {
  const grid = rows.map((row) =>
    `<div class="row">${row.map((c) =>
      `<a href="#" class="ch" data-ch="${c.replace(/"/g, '&quot;')}">${
        c.replace(/&/g, '&amp;').replace(/</g, '&lt;')}</a>`).join('')}</div>`
  ).join('\n');

  return `<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {
    font-family: ${fontCss};
    background: var(--vscode-editor-background);
    color: var(--vscode-editor-foreground);
    margin: 0; padding: 8px; user-select: none;
  }
  .row { white-space: nowrap; line-height: 1.1; }
  .ch {
    display: inline-block; text-decoration: none; font-size: 22px;
    padding: 2px 3px; color: inherit; cursor: pointer;
  }
  .ch:hover { background: var(--vscode-editor-selectionBackground); }
  .hint { font-family: var(--vscode-font-family); font-size: 11px;
          opacity: .7; margin-bottom: 6px; }
</style></head>
<body>
  <div class="hint">Click a character to insert it at the cursor.</div>
  ${grid}
  <script>
    const vs = acquireVsCodeApi();
    for (const a of document.querySelectorAll('.ch')) {
      a.addEventListener('click', (e) => {
        e.preventDefault();
        vs.postMessage({ ch: a.dataset.ch });
      });
    }
  </script>
</body></html>`;
}

function showPalette(context, which, title) {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showErrorMessage('ATASCII: no active editor.');
    return;
  }
  const rows = data(context).palettes[which];
  if (!rows) {
    vscode.window.showErrorMessage(`ATASCII: unknown palette "${which}".`);
    return;
  }

  const font = vscode.workspace.getConfiguration('ataritools')
    .get('atasciiFont',
      "AtariClassic-Regular, 'Atari Classic', 'EightBit Atari', monospace");

  const panel = vscode.window.createWebviewPanel(
    'ataritools.atascii', title,
    { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
    { enableScripts: true, retainContextWhenHidden: true });

  panel.webview.html = paletteHtml(rows, font);

  panel.webview.onDidReceiveMessage(async (msg) => {
    if (!msg || typeof msg.ch !== 'string') return;
    // Insert into the editor the palette was opened for, not whatever has
    // focus now -- the WebView itself can hold focus.
    const target = vscode.window.visibleTextEditors
      .find((e) => e.document === editor.document) || editor;
    await target.edit((b) => {
      for (const sel of target.selections) b.replace(sel, msg.ch);
    });
  }, undefined, context.subscriptions);

  return panel;
}

function register(context) {
  const reg = (id, fn) => context.subscriptions.push(
    vscode.commands.registerCommand(id, fn));

  reg('ataritools.atascii.insertInverted',
    () => showPalette(context, 'inverted', 'ATASCII: Inverted'));
  reg('ataritools.atascii.insertSpecial',
    () => showPalette(context, 'special', 'ATASCII: Special'));
  reg('ataritools.atascii.insertDrawing',
    () => showPalette(context, 'drawing', 'ATASCII: Drawing'));
  reg('ataritools.atascii.invertSelection',
    () => invertSelection(context));
}

module.exports = { register, invertText, paletteHtml, data };
