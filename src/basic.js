/**
 * AtariTools - BASIC build/run
 *
 * Thin wrapper around the Python tokenizer (basic.py), which is the single
 * source of truth shared with the Sublime plugin. Nothing here reimplements
 * tokenization -- it shells out and parses the result.
 */

'use strict';

const vscode = require('vscode');
const cp = require('child_process');
const path = require('path');
const fs = require('fs');

function config() {
  return vscode.workspace.getConfiguration('ataritools');
}

/**
 * Locate basic.py. Preference order:
 *   1. the ataritools.basicToolPath setting
 *   2. the copy bundled in the extension (python/basic.py)
 *   3. a sibling 6502-Tools checkout, for shared-development convenience
 *
 * In this repo python/ holds REAL files, not a symlink: a .vsix cannot
 * contain a link that escapes the extension root, so the tokenizer is
 * vendored here and synced with tools/sync_tokenizer.sh.
 */
function basicToolPath(context) {
  const configured = config().get('basicToolPath', '');
  if (configured && fs.existsSync(configured)) return configured;

  const bundled = path.join(context.extensionPath, 'python', 'basic.py');
  if (fs.existsSync(bundled)) return bundled;

  const shared = path.join(
    context.extensionPath, '..', '6502-Tools',
    'Sublime', 'AtariTools', 'basic', 'basic.py');
  if (fs.existsSync(shared)) return path.resolve(shared);

  return null;
}

/** Run basic.py and resolve with {code, stdout, stderr}. */
function runTool(context, args) {
  return new Promise((resolve, reject) => {
    const tool = basicToolPath(context);
    if (!tool) {
      reject(new Error(
        'Could not find basic.py. Set "ataritools.basicToolPath" in settings.'));
      return;
    }
    const py = config().get('pythonPath', 'python3');
    // -B: do not litter the source tree with __pycache__
    cp.execFile(py, ['-B', tool, ...args], {
      cwd: path.dirname(tool),
      maxBuffer: 16 * 1024 * 1024,
    }, (err, stdout, stderr) => {
      resolve({ code: err ? (err.code || 1) : 0, stdout, stderr });
    });
  });
}

/** Ask the tokenizer for the full JSON description of a .LST file. */
async function inspectFile(context, lstPath) {
  const r = await runTool(context, ['--json', lstPath]);
  if (r.code !== 0 && !r.stdout.trim()) {
    throw new Error(r.stderr.trim() || `basic.py exited ${r.code}`);
  }
  try {
    return JSON.parse(r.stdout);
  } catch (e) {
    throw new Error(
      `basic.py did not return valid JSON.\n${r.stdout.slice(0, 400)}`);
  }
}

function activeLst() {
  const ed = vscode.window.activeTextEditor;
  if (!ed || ed.document.languageId !== 'ataribasic') {
    vscode.window.showErrorMessage('AtariTools: open an Atari BASIC .LST/.ULST file first.');
    return null;
  }
  return ed.document;
}

/** Where the built .BAS should go: the configured H: dir, else alongside the source. */
function outputPathFor(doc) {
  const name = path.basename(doc.fileName).replace(/\.[^.]*$/, '');
  const atariName = name.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8) || 'PROGRAM';
  const hd = config().get('hardDrivePath', '');
  const dir = hd && fs.existsSync(hd) ? hd : path.dirname(doc.fileName);
  return path.join(dir, atariName + '.BAS');
}

async function buildCurrent(context) {
  const doc = activeLst();
  if (!doc) return null;
  if (doc.isDirty) await doc.save();

  const out = outputPathFor(doc);
  const r = await runTool(context, [doc.fileName, '-o', out]);

  if (r.code !== 0) {
    vscode.window.showErrorMessage(
      `AtariTools build failed: ${(r.stderr || r.stdout).trim().split('\n').pop()}`);
    return null;
  }
  const size = fs.existsSync(out) ? fs.statSync(out).size : 0;
  vscode.window.setStatusBarMessage(
    `AtariTools: built ${path.basename(out)} (${size} bytes)`, 5000);
  return out;
}

/** Path to the ATASCII converter that sits beside basic.py. */
function atasciiToolPath(context) {
  return path.join(context.extensionPath, 'python', 'atascii.py');
}

/**
 * Stage a listing onto the emulator's H: drive as ATASCII.
 *
 * This mirrors helper/AtariBASIC-run.sh: the Atari only understands ATASCII
 * (and only knows the .LST extension), so a Unicode .ULST is converted with
 * `atascii.py -s -u` and staged as .LST. `-s` strips host-only comments.
 * Returns the staged path, or null if conversion failed.
 */
function stageAtascii(context, doc, hdDir) {
  const base = path.basename(doc.fileName).replace(/\.(ulst|lst|ataribas\w*)$/i, '');
  const staged = path.join(hdDir, `${base.toUpperCase().slice(0, 8)}.LST`);

  const r = cp.spawnSync(config().get('pythonPath', 'python3'),
    ['-B', atasciiToolPath(context), '-s', '-u', doc.fileName],
    { encoding: 'buffer', maxBuffer: 16 * 1024 * 1024 });

  if (r.status !== 0) {
    vscode.window.showErrorMessage(
      `AtariTools: ATASCII conversion failed: ${
        (r.stderr || Buffer.alloc(0)).toString().trim().split('\n').pop()}`);
    return null;
  }
  fs.writeFileSync(staged, r.stdout);   // bytes, not text -- ATASCII is 8-bit
  return staged;
}

async function runCurrent(context) {
  const doc = activeLst();
  if (!doc) return;
  if (doc.isDirty) await doc.save();

  const emu = config().get('emulatorPath', 'atari800');
  const model = config().get('emulatorModel', '-atari');
  const hd = config().get('hardDrivePath', '');

  // The Atari speaks ATASCII, not Unicode. When an H: drive is configured,
  // stage a converted .LST there (mirroring helper/AtariBASIC-run.sh) so a
  // .ULST runs correctly; otherwise fall back to running the file as-is,
  // which is only right for a listing that is already ATASCII.
  let target = doc.fileName;
  if (hd && fs.existsSync(hd)) {
    const staged = stageAtascii(context, doc, hd);
    if (!staged) return;
    target = staged;
  } else if (/\.ulst$/i.test(doc.fileName)) {
    vscode.window.showWarningMessage(
      'AtariTools: set ataritools.hardDrivePath so .ULST can be converted ' +
      'to ATASCII before running.');
    return;
  }

  const args = [];
  if (config().get('turbo', true)) args.push('-turbo');
  if (model) args.push(model);
  args.push('-basic', '-run', target);

  const term = vscode.window.createTerminal({ name: 'atari800' });
  term.sendText(`${emu} ${args.map((a) => `'${a}'`).join(' ')}`);
  term.show(true);
}

module.exports = { buildCurrent, runCurrent, inspectFile, basicToolPath };
