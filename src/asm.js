/**
 * AtariTools - 6502 assembly / C build for the Atari 400/800
 *
 * Mirrors the Sublime "Atari 800" build target (helper/Atari800-build.sh),
 * which is the real workflow:
 *
 *     ca65 $file -l $base.txt -o $base.o
 *     ld65 -o $base.bin -C $base.lnk $base.o
 *     atari800 -atari -nobasic -run $base.bin
 *
 * Two things this gets right that a naive `cl65` build does not:
 *
 *   1. A per-project **linker config** (`$base.lnk`, else `$base.cfg`) decides
 *      the memory map. Atari 8-bit work is full of "this must live at $2000 /
 *      below the display list / in page 6" constraints, so the link config is
 *      part of the source, not a detail to paper over. Missing config is a
 *      hard error -- guessing a memory map silently produces a binary that
 *      loads in the wrong place.
 *   2. ca65 emits an xo65 OBJECT file; only ld65 produces a loadable binary.
 *      (The Sublime "Atari ca65" target names ca65's output .xex, which is
 *      misleading -- `file` reports "xo65 object, version 17".)
 *
 * C builds go through cl65, which drives cc65 + ca65 + ld65 in one step.
 * BASIC has its own path (basic.js) via the Python tokenizer.
 */

'use strict';

const vscode = require('vscode');
const cp = require('child_process');
const path = require('path');
const fs = require('fs');

const ASM_EXT = ['.s', '.asm', '.inc', '.a65', '.6502'];

let channel = null;
function out() {
  if (!channel) channel = vscode.window.createOutputChannel('AtariTools');
  return channel;
}

function cfg(key, dflt) {
  return vscode.workspace.getConfiguration('ataricode800').get(key, dflt);
}

/** Run one tool, streaming its output. Rejects with a useful message. */
function runTool(tool, args, cwd) {
  return new Promise((resolve, reject) => {
    out().appendLine(`> ${tool} ${args.join(' ')}`);
    cp.execFile(tool, args, { cwd }, (err, stdout, stderr) => {
      if (stdout) out().append(stdout);
      if (stderr) out().append(stderr);
      if (!err) return resolve();
      reject(new Error(err.code === 'ENOENT'
        ? `${tool} not found. Install the cc65 toolchain ` +
          `("sudo port install cc65" or "brew install cc65"), or set its path in settings.`
        : (stderr.trim() || err.message)));
    });
  });
}

/**
 * Locate the linker config for an assembly project: <base>.lnk, else
 * <base>.cfg. Returns null when neither exists.
 */
function findLinkConfig(base) {
  for (const ext of ['.lnk', '.cfg']) {
    const p = base + ext;
    if (fs.existsSync(p)) return p;
  }
  return null;
}

/**
 * Find cc65's stock atari-asm.cfg to seed a new project's linker config.
 * Checks the configured dir first, then the usual MacPorts/Homebrew spots.
 */
function findStockConfig() {
  const dirs = [
    String(cfg('cc65CfgDir', '')).trim(),
    '/opt/local/share/cc65/cfg',
    '/opt/homebrew/share/cc65/cfg',
    '/usr/local/share/cc65/cfg',
  ].filter(Boolean);
  for (const d of dirs) {
    const p = path.join(d, 'atari-asm.cfg');
    if (fs.existsSync(p)) return p;
  }
  return null;
}

/**
 * Build the active file for the Atari 800.
 * Returns the output binary path, or null on failure.
 */
async function build() {
  const ed = vscode.window.activeTextEditor;
  if (!ed) { vscode.window.showErrorMessage('AtariTools: no active editor.'); return null; }

  const file = ed.document.fileName;
  if (ed.document.isDirty) await ed.document.save();

  const dir = path.dirname(file);
  const ext = path.extname(file).toLowerCase();
  const base = path.join(dir, path.basename(file, path.extname(file)));

  out().show(true);
  out().appendLine(`--- Building ${path.basename(file)} ---`);

  let outfile;
  try {
    if (ext === '.c') {
      outfile = `${base}.xex`;
      await runTool(cfg('cl65Path', 'cl65'),
        ['-tatari', '-Catari-xex.cfg', file, '-o', outfile], dir);
    } else if (ASM_EXT.includes(ext)) {
      const lnk = findLinkConfig(base);
      if (!lnk) {
        // Rather than dead-ending, offer the stock cc65 Atari config. The
        // memory map belongs with the project, so we copy it in as a starting
        // point the user can edit -- never silently link against a guess.
        const msg = `No linker config for ${path.basename(base)}. ` +
                    `Create one from the cc65 Atari template?`;
        const pick = await vscode.window.showWarningMessage(
          msg, 'Create .lnk', 'Cancel');
        if (pick !== 'Create .lnk') {
          out().appendLine('BUILD CANCELLED: no linker config.');
          return null;
        }
        const tpl = findStockConfig();
        if (!tpl) {
          const m = 'cc65 atari-asm.cfg template not found; set ataricode800.cc65CfgDir.';
          out().appendLine(`BUILD FAILED: ${m}`);
          vscode.window.showErrorMessage(`AtariTools: ${m}`);
          return null;
        }
        fs.copyFileSync(tpl, `${base}.lnk`);
        out().appendLine(`Created ${base}.lnk from ${tpl}`);
      }
      const linkCfg = findLinkConfig(base);
      outfile = `${base}.bin`;
      await runTool(cfg('ca65Path', 'ca65'),
        [file, '-l', `${base}.txt`, '-o', `${base}.o`], dir);
      // Link against the target library so the config's __EXEHDR__ /
      // __AUTOSTART__ imports resolve -- without it ld65 fails with
      // "unresolved external", which looks like a source error but isn't.
      const ldArgs = ['-o', outfile, '-C', linkCfg, `${base}.o`];
      const lib = String(cfg('targetLib', 'atari.lib')).trim();
      if (lib) ldArgs.push(lib);
      await runTool(cfg('ld65Path', 'ld65'), ldArgs, dir);
    } else {
      vscode.window.showErrorMessage(
        `AtariTools: don't know how to build "${path.basename(file)}".`);
      return null;
    }
  } catch (e) {
    out().appendLine(`BUILD FAILED: ${e.message}`);
    vscode.window.showErrorMessage(`AtariTools build failed: ${e.message}`);
    return null;
  }

  if (!fs.existsSync(outfile)) {
    const msg = 'toolchain reported success but produced no binary';
    out().appendLine(`BUILD FAILED: ${msg}`);
    vscode.window.showErrorMessage(`AtariTools: ${msg}`);
    return null;
  }

  out().appendLine(`Built ${outfile}`);
  vscode.window.showInformationMessage(`AtariTools: built ${path.basename(outfile)}`);
  return outfile;
}

/**
 * Build, then boot the binary in atari800.
 *
 * `-nobasic` matters: with the BASIC cartridge enabled the machine comes up
 * in BASIC and a binary load behaves differently. The Sublime script passes
 * `-atari -nobasic -run`; we keep that and append the user's display options.
 */
async function buildAndRun() {
  const bin = await build();
  if (!bin) return;

  const args = [];
  const model = String(cfg('emulatorModel', '-atari')).trim();
  if (model) args.push(model);
  args.push('-nobasic');
  if (cfg('turbo', false)) args.push('-turbo');
  for (const opt of String(cfg('emulatorOptions', '')).split(/\s+/).filter(Boolean)) {
    args.push(opt);
  }
  args.push('-run', bin);

  out().appendLine(`> ${cfg('emulatorPath', 'atari800')} ${args.join(' ')}`);
  const proc = cp.spawn(cfg('emulatorPath', 'atari800'), args,
    { detached: true, stdio: 'ignore' });
  proc.on('error', (e) => {
    vscode.window.showErrorMessage(`AtariTools: ${e.code === 'ENOENT'
      ? 'atari800 not found. Set ataricode800.emulatorPath.' : e.message}`);
  });
  proc.unref();
}

module.exports = { build, buildAndRun, findLinkConfig, findStockConfig, ASM_EXT };
