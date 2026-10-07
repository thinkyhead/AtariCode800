/**
 * AtariCode800 - BASIC build/run
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
  return vscode.workspace.getConfiguration('ataricode800');
}

/**
 * Locate basic.py. Preference order:
 *   1. the ataricode800.basicToolPath setting
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
        'Could not find basic.py. Set "ataricode800.basicToolPath" in settings.'));
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
    vscode.window.showErrorMessage('AtariCode800: open an Atari BASIC .LST/.ULST file first.');
    return null;
  }
  return ed.document;
}

/**
 * The H: staging directory, or null when output belongs beside the source:
 * the "Stage LST/BAS on Hard Drive" option is off, the path is empty, or the
 * directory does not exist.
 */
function stagingDir() {
  if (!config().get('stageOnHardDrive', false)) return null;
  const hd = (config().get('hardDrivePath', '') || '').trim();
  return hd && fs.existsSync(hd) ? hd : null;
}

/** Where the built .BAS should go: the H: staging dir, else beside the source. */
function outputPathFor(doc) {
  const name = path.basename(doc.fileName).replace(/\.[^.]*$/, '');
  const atariName = name.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8) || 'PROGRAM';
  const dir = stagingDir() || path.dirname(doc.fileName);
  return path.join(dir, atariName + '.BAS');
}

/** Builds in flight, keyed by source path: a second build of the same file
 *  while one is running is ignored (it would race on the same output). */
const building = new Set();

async function buildCurrent(context, { quiet = false } = {}) {
  const doc = activeLst();
  if (!doc) return null;
  const key = doc.fileName;
  if (building.has(key)) {
    vscode.window.setStatusBarMessage(
      `AtariCode800: ${path.basename(key)} is already building`, 3000);
    return null;
  }
  building.add(key);
  try {
    if (doc.isDirty) await doc.save();
    const out = outputPathFor(doc);
    // Animated progress while basic.py runs (status bar spinner + toast).
    const r = await vscode.window.withProgress({
      location: vscode.ProgressLocation.Notification,
      title: `AtariCode800: tokenizing ${path.basename(doc.fileName)} \u2192 ${path.basename(out)}\u2026`,
    }, () => runTool(context, [doc.fileName, '-o', out]));

    if (r.code !== 0 || !fs.existsSync(out)) {
      vscode.window.showErrorMessage(
        `AtariCode800 build failed: ${(r.stderr || r.stdout || 'no output written').trim().split('\n').pop()}`);
      return null;
    }
    const size = fs.statSync(out).size;
    if (!quiet) {
      const where = path.dirname(out) === path.dirname(doc.fileName)
        ? 'beside the source' : `in ${path.dirname(out)}`;
      vscode.window.showInformationMessage(
        `AtariCode800: created ${path.basename(out)} (${size} bytes) ${where}.`,
        'Reveal').then((pick) => {
        if (pick === 'Reveal') {
          vscode.commands.executeCommand('revealFileInOS', vscode.Uri.file(out));
        }
      });
    }
    return out;
  } finally {
    building.delete(key);
  }
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
  // Never overwrite the source itself (FOO.lst vs FOO.LST on a
  // case-insensitive volume when converting beside the source).
  if (path.resolve(staged).toLowerCase() === path.resolve(doc.fileName).toLowerCase()) {
    return doc.fileName;
  }

  const r = cp.spawnSync(config().get('pythonPath', 'python3'),
    ['-B', atasciiToolPath(context), '-s', '-u', doc.fileName],
    { encoding: 'buffer', maxBuffer: 16 * 1024 * 1024 });

  if (r.status !== 0) {
    vscode.window.showErrorMessage(
      `AtariCode800: ATASCII conversion failed: ${
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

  const hd = stagingDir();

  // The Atari speaks ATASCII, not Unicode, so a .ULST is converted to an
  // ATASCII .LST first (mirroring helper/AtariBASIC-run.sh): into the H:
  // staging dir when staging is on, otherwise beside the source. An ATASCII
  // .LST runs as-is unless it is being staged.
  let target = doc.fileName;
  const isUnicode = /\.ulst$/i.test(doc.fileName);
  if (hd || isUnicode) {
    const staged = stageAtascii(context, doc, hd || path.dirname(doc.fileName));
    if (!staged) return;
    target = staged;
  }

  launch(target);
}

/** Launch atari800 with BASIC enabled, running `target` (.LST or .BAS). */
function launch(target) {
  const emu = config().get('emulatorPath', 'atari800');
  const model = config().get('emulatorModel', '-atari');
  const args = [];
  if (config().get('turbo', false)) args.push('-turbo');
  if (model) args.push(model);
  args.push('-basic', '-run', target);
  const term = vscode.window.createTerminal({ name: 'atari800' });
  term.sendText(`${emu} ${args.map((a) => `'${a}'`).join(' ')}`);
  term.show(true);
}

/** ULST/LST -> tokenized .BAS (H: staging dir or beside the source), then run it. */
async function runBasCurrent(context) {
  const out = await buildCurrent(context, { quiet: true });
  if (!out) return;
  vscode.window.setStatusBarMessage(`AtariCode800: running ${path.basename(out)}`, 5000);
  launch(out);
}

module.exports = { buildCurrent, runCurrent, runBasCurrent, inspectFile, basicToolPath };
