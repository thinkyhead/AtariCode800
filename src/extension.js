/**
 * AtariCode800 for VSCode
 *
 * Atari 8-bit development suite. This file wires up commands; the real work
 * lives in the modules under src/.
 */

'use strict';

const vscode = require('vscode');
const basic = require('./basic');
const asm = require('./asm');
const inspector = require('./inspector');
const atascii = require('./atascii');
const download = require('./download');
const invalid = require('./invalid');

/** Build/Run dispatch on the active document's language.
 *  BASIC goes through the Python tokenizer; asm and C go to the cc65 tools. */
function isBasic() {
  const ed = vscode.window.activeTextEditor;
  return ed && ed.document.languageId === 'ataribasic';
}

function activate(context) {
  const sub = context.subscriptions;
  const reg = (id, fn) => sub.push(vscode.commands.registerCommand(id, fn));

  reg('ataricode800.build', () => (isBasic() ? basic.buildCurrent(context) : asm.build()));
  reg('ataricode800.run', () => (isBasic() ? basic.runCurrent(context) : asm.buildAndRun()));
  reg('ataricode800.runBas', () => basic.runBasCurrent(context));
  reg('ataricode800.inspect', () => inspector.open(context));
  inspector.register(context);
  reg('ataricode800.gotBasic', () => download.gotBasic(context));

  atascii.register(context);
  invalid.register(context);

  // Keep an open Inspector in sync with edits to the active .LST.
  sub.push(vscode.workspace.onDidSaveTextDocument((doc) => {
    if (doc.languageId === 'ataribasic') inspector.refreshIfOpen(context, doc);
  }));

  context.subscriptions.push(
    vscode.window.setStatusBarMessage('AtariCode800 ready', 3000)
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
