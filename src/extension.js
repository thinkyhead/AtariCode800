/**
 * AtariTools for VSCode
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

/** Build/Run dispatch on the active document's language.
 *  BASIC goes through the Python tokenizer; asm and C go to the cc65 tools. */
function isBasic() {
  const ed = vscode.window.activeTextEditor;
  return ed && ed.document.languageId === 'ataribasic';
}

function activate(context) {
  const sub = context.subscriptions;
  const reg = (id, fn) => sub.push(vscode.commands.registerCommand(id, fn));

  reg('ataritools.build', () => (isBasic() ? basic.buildCurrent(context) : asm.build()));
  reg('ataritools.run', () => (isBasic() ? basic.runCurrent(context) : asm.buildAndRun()));
  reg('ataritools.inspect', () => inspector.open(context));

  atascii.register(context);

  // Keep an open Inspector in sync with edits to the active .LST.
  sub.push(vscode.workspace.onDidSaveTextDocument((doc) => {
    if (doc.languageId === 'ataribasic') inspector.refreshIfOpen(context, doc);
  }));

  context.subscriptions.push(
    vscode.window.setStatusBarMessage('AtariTools ready', 3000)
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
