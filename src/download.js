/**
 * "Got BASIC?" -- fetch Atari BASIC programs from the 6502-Tools repo.
 *
 * Populates a QuickPick from Crossover/AtariBASIC on GitHub and downloads the
 * chosen listing into the workspace. Modelled on AutoBuildMarlin's
 * abm/downloader.js, which does the same job for Marlin configurations:
 * list with the git/trees API, fetch blobs from raw.githubusercontent.com.
 *
 * Why the trees API and not the contents API: one request returns the whole
 * recursive listing, so a five-subfolder library costs a single round trip
 * instead of one per directory.
 */

'use strict';

const vscode = require('vscode');
const path = require('path');
const https = require('https');
const zlib = require('zlib');

const OWNER = 'thinkyhead';
const REPO = '6502-Tools';
// Matched case-INSENSITIVELY. Git records this as 'Crossover/AtariBasic', but a
// case-insensitive macOS checkout displays 'AtariBASIC', so hard-coding either
// spelling makes the GitHub API (which IS case-sensitive) return nothing.
const FOLDER = 'Crossover/AtariBasic';

// Listings only. .ULST is Unicode-authored, .LST is ATASCII; both are openable.
const LISTING_RE = /\.(ULST|LST)$/i;

function config() {
  return vscode.workspace.getConfiguration('ataricode800');
}

/**
 * GET a URL as text, following redirects and transparently gunzipping.
 *
 * GitHub redirects both API and raw endpoints, and gzips large tree responses,
 * so both have to be handled or the body arrives empty or as binary noise.
 */
function httpGetText(url) {
  return new Promise((resolve, reject) => {
    const fetch = (currentUrl, redirects) => {
      if (redirects > 5) {
        reject(new Error(`Too many redirects while fetching ${url}`));
        return;
      }
      const headers = {
        'User-Agent': 'AtariCode800',
        'Accept': 'application/vnd.github+json',
        'Accept-Encoding': 'gzip, deflate',
      };
      // An optional token only raises the rate limit; the repo is public.
      const token = config().get('githubToken', '');
      if (token) headers.Authorization = `Bearer ${token}`;

      const request = https.get(currentUrl, { headers }, (res) => {
        try {
          const status = res.statusCode || 0;
          if (status >= 300 && status < 400 && res.headers.location) {
            const next = new URL(res.headers.location, currentUrl).toString();
            res.resume();
            fetch(next, redirects + 1);
            return;
          }

          let stream = res;
          const enc = String(res.headers['content-encoding'] || '').toLowerCase();
          if (enc.includes('gzip')) stream = res.pipe(zlib.createGunzip());
          else if (enc.includes('deflate')) stream = res.pipe(zlib.createInflate());

          const chunks = [];
          stream.on('data', (d) => chunks.push(Buffer.isBuffer(d) ? d : Buffer.from(d)));
          stream.on('error', reject);
          stream.on('end', () => {
            const body = Buffer.concat(chunks);
            if (status !== 200) {
              reject(new Error(
                `HTTP ${status} for ${currentUrl}: ${body.toString('utf8').slice(0, 200)}`));
              return;
            }
            resolve(body);
          });
        }
        catch (err) { reject(err); }
      });
      request.on('error', reject);
    };
    fetch(url, 0);
  });
}

async function githubApiJson(url) {
  return JSON.parse((await httpGetText(url)).toString('utf8'));
}

/** Every listing under FOLDER, as [{path, label, folder, size}]. */
async function loadListings(branch) {
  const url = `https://api.github.com/repos/${OWNER}/${REPO}`
    + `/git/trees/${encodeURIComponent(branch)}?recursive=1`;
  const tree = await githubApiJson(url);
  if (tree.truncated) {
    // Only reachable if the repo grows enormous; say so rather than silently
    // offering a partial list.
    throw new Error('GitHub tree response was truncated; cannot list reliably.');
  }
  const prefix = (FOLDER + '/').toLowerCase();
  return (tree.tree || [])
    .filter((n) => n.type === 'blob'
                && n.path.toLowerCase().startsWith(prefix)
                && LISTING_RE.test(n.path))
    .map((n) => {
      const rel = n.path.slice(prefix.length);
      const dir = path.posix.dirname(rel);
      return {
        path: n.path,
        rel,
        label: path.posix.basename(rel),
        folder: dir === '.' ? '' : dir,
        size: n.size || 0,
      };
    })
    .sort((a, b) => (a.folder || '\u0000').localeCompare(b.folder || '\u0000')
                 || a.label.localeCompare(b.label, undefined, { numeric: true }));
}

function rawUrl(branch, repoPath) {
  const safeBranch = encodeURIComponent(branch);
  const safePath = repoPath.split('/').map(encodeURIComponent).join('/');
  return `https://raw.githubusercontent.com/${OWNER}/${REPO}/${safeBranch}/${safePath}`;
}

/**
 * Prompt for a program, download it, and open it.
 *
 * The branch is tried as 'main' then 'master' so the command works whichever
 * the repo uses, instead of failing on a guess.
 */
async function gotBasic(context) {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || !folders.length) {
    vscode.window.showErrorMessage(
      'Open a folder first: downloaded programs are saved into the workspace.');
    return;
  }

  let branch = config().get('sourceBranch', '');
  let items = null;
  let lastErr = null;

  await vscode.window.withProgress({
    location: vscode.ProgressLocation.Notification,
    title: 'Got BASIC? Fetching the program list…',
  }, async () => {
    // 6502-Tools' default branch is master. Try it first, then main, so the
    // command keeps working if the repo is ever renamed to the newer default.
    for (const b of (branch ? [branch] : ['master', 'main'])) {
      try {
        items = await loadListings(b);
        branch = b;
        return;
      }
      catch (err) { lastErr = err; }
    }
  });

  if (!items) {
    vscode.window.showErrorMessage(
      `Could not list programs: ${lastErr ? lastErr.message : 'unknown error'}`);
    return;
  }
  if (!items.length) {
    vscode.window.showWarningMessage(`No .LST or .ULST files found in ${FOLDER}.`);
    return;
  }

  const picks = items.map((it) => ({
    label: it.label,
    description: it.folder || undefined,
    detail: it.size ? `${(it.size / 1024).toFixed(1)} KB` : undefined,
    item: it,
  }));

  const chosen = await vscode.window.showQuickPick(picks, {
    matchOnDescription: true,
    placeHolder: `Select an Atari BASIC program from ${OWNER}/${REPO} (${branch})`,
  });
  if (!chosen) return;

  const it = chosen.item;
  const destDir = folders[0].uri;
  const dest = vscode.Uri.joinPath(destDir, it.label);

  // Never clobber silently -- the user may have edited a previous download.
  try {
    await vscode.workspace.fs.stat(dest);
    const answer = await vscode.window.showWarningMessage(
      `${it.label} already exists in the workspace.`,
      { modal: true }, 'Overwrite', 'Cancel');
    if (answer !== 'Overwrite') return;
  }
  catch { /* does not exist, which is the normal case */ }

  try {
    const body = await vscode.window.withProgress({
      location: vscode.ProgressLocation.Notification,
      title: `Downloading ${it.label}…`,
    }, () => httpGetText(rawUrl(branch, it.path)));

    // Write bytes verbatim. A .LST is ATASCII with $9B terminators and must
    // NOT be decoded as UTF-8 on the way through.
    await vscode.workspace.fs.writeFile(dest, body);

    const doc = await vscode.workspace.openTextDocument(dest);
    await vscode.window.showTextDocument(doc);
  }
  catch (err) {
    vscode.window.showErrorMessage(`Download failed: ${err.message}`);
  }
}

module.exports = { gotBasic, loadListings, rawUrl, httpGetText };
