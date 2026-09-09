/**
 * Git diff plumbing shared by the TypeScript gates — the JS counterpart of
 * precommit_git_diff.py, and deliberately the same shape.
 *
 * Two callers with two notions of "what this change touched": pre-commit scopes
 * to the index, an orchestration run scopes to the working tree because an
 * agent's edits are uncommitted when a transition gate fires. `diffScope` names
 * which one; everything downstream consumes the same parsed shape either way.
 */

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

function git(args, cwd) {
  try {
    return execFileSync("git", args, {
      cwd,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (err) {
    // Returning "" scopes nothing, which makes the gate PASS vacuously — the
    // exact failure mode these gates exist to stop. Callers still need a string,
    // so keep the contract but never let it happen quietly.
    console.error(
      `warning: git ${args[0] ?? ""} failed in ${cwd}; the silent-failure gate ` +
        `has no diff to scope against and may report a vacuous pass: ${err.message}`,
    );
    return "";
  }
}

/** git-diff selector per scope; mirrors precommit_git_diff.py's `_scope_args`. */
function scopeArgs(diffScope, baseRef) {
  if (diffScope === "worktree") return ["HEAD"];
  if (diffScope === "branch") return [`${baseRef}...HEAD`];
  return ["--cached"];
}

function untrackedPaths(repoRoot) {
  return git(["ls-files", "--others", "--exclude-standard"], repoRoot)
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
}

/**
 * Files this run should read when it was given no explicit list (gate mode).
 * Untracked files are included under `worktree` for the same reason the Python
 * gate includes them: a new file an agent just wrote is the least reviewed code
 * in the change, and `git diff` never lists it.
 */
function changedPaths(repoRoot, diffScope, baseRef) {
  const out = git(
    ["diff", ...scopeArgs(diffScope, baseRef), "--name-only", "--diff-filter=ACMR"],
    repoRoot,
  );
  const paths = out
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  if (diffScope === "worktree") paths.push(...untrackedPaths(repoRoot));
  return [...new Set(paths)].filter((p) => /\.(ts|tsx)$/.test(p));
}

function stagedAdditions(repoRel, repoRoot, diffScope, baseRef, isUntracked, lineCount) {
  if (isUntracked) {
    // Nothing to diff against: the whole file is new.
    const added = new Set();
    for (let i = 1; i <= lineCount; i += 1) added.add(i);
    return { added, addedCount: lineCount, deletedCount: 0 };
  }
  const diff = git(
    ["diff", ...scopeArgs(diffScope, baseRef), "--no-color", "-U0", "--", repoRel],
    repoRoot,
  );
  if (!diff) {
    return { added: new Set(), addedCount: 0, deletedCount: 0 };
  }
  const added = new Set();
  let addedCount = 0;
  let deletedCount = 0;
  let newLine = 0;
  for (const line of diff.split("\n")) {
    const hunk = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/.exec(line);
    if (hunk) {
      newLine = Number(hunk[1]);
      continue;
    }
    if (line.startsWith("+") && !line.startsWith("+++")) {
      added.add(newLine);
      addedCount += 1;
      newLine += 1;
    } else if (line.startsWith("-") && !line.startsWith("---")) {
      deletedCount += 1;
    }
  }
  return { added, addedCount, deletedCount };
}

/** Where this repo keeps its TypeScript; detected, never assumed. */
function tsSourceRoot(repoRoot) {
  for (const candidate of ["client/src", "src", "app", "frontend/src"]) {
    const full = path.resolve(repoRoot, candidate);
    if (fs.existsSync(full)) return full;
  }
  return path.resolve(repoRoot);
}

function isTestFile(filePath) {
  return (
    filePath.includes("/__tests__/") ||
    filePath.includes(".test.") ||
    filePath.includes(".spec.")
  );
}

/** Shared argv shape for both gates: `--repo`, `--scope`, `--base`, positionals. */
function parseArgv(argv) {
  const files = [];
  let repoArg = null;
  let diffScope = "staged";
  let baseRef = "main";
  let scanAll = false;
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--repo" && argv[i + 1]) repoArg = argv[(i += 1)];
    else if (argv[i] === "--scope" && argv[i + 1]) diffScope = argv[(i += 1)];
    else if (argv[i] === "--base" && argv[i + 1]) baseRef = argv[(i += 1)];
    else if (argv[i] === "--all") scanAll = true;
    else if (/\.(ts|tsx)$/.test(argv[i])) files.push(argv[i]);
  }
  if (!["staged", "worktree", "branch"].includes(diffScope)) diffScope = "staged";
  const repoRoot = repoArg ? path.resolve(repoArg) : process.cwd();
  const label = diffScope === "staged" && !repoArg ? "pre-commit" : "gate";
  return { files, repoRoot, diffScope, baseRef, label, scanAll };
}

/** Every .ts/.tsx under a root, for `--all` audit runs. */
function allSourceFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...allSourceFiles(full));
    else if (/\.(ts|tsx)$/.test(entry.name)) out.push(full);
  }
  return out;
}

/**
 * The file list a gate should read: the explicit argv list, or the diff confined
 * to the repo's detected TypeScript root (mirroring the lefthook glob).
 */
function filesToCheck({ files, repoRoot, diffScope, baseRef, scanAll }) {
  if (files.length > 0) return files;
  const sourceRoot = tsSourceRoot(repoRoot);
  if (scanAll) return allSourceFiles(sourceRoot);
  return changedPaths(repoRoot, diffScope, baseRef)
    .map((rel) => path.resolve(repoRoot, rel))
    .filter((full) => full.startsWith(`${sourceRoot}${path.sep}`));
}

module.exports = {
  git,
  scopeArgs,
  untrackedPaths,
  changedPaths,
  stagedAdditions,
  tsSourceRoot,
  isTestFile,
  parseArgv,
  filesToCheck,
};
