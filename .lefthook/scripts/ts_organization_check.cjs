#!/usr/bin/env node
/**
 * TypeScript/React organization guardrails for staged client files.
 *
 * Checks (on staged .ts/.tsx files):
 *   1. File size limit — only when this commit *grows* an already-over-limit file
 *   2. No direct fetch/axios calls in .tsx — only on newly added lines
 *   3. Within-file duplicate function bodies (>= MIN_DUPLICATE_BODY_LINES)
 *   4. Cross-codebase DRY against the rest of client/src
 *   5. Barrel index.ts size limit (growth-only)
 *   6. No inline `instanceof Error` ternary — only on newly added lines
 *
 * Diff-scoped size/API rules match server py_organization_check.py so existing
 * debt (e.g. Dashboard.tsx) does not block unrelated edits.
 */

const fs = require("fs");
const path = require("path");
const { createRequire } = require("module");
const { execFileSync } = require("child_process");

const clientRoot = path.resolve(__dirname, "../../client");
const requireFromClient = createRequire(path.join(clientRoot, "package.json"));
const { parse } = requireFromClient("@typescript-eslint/typescript-estree");

const MAX_FILE_LINES = 1200;
const MAX_TSX_FILE_LINES = 1200;
const MAX_INDEX_LINES = 80;
const MIN_DUPLICATE_BODY_LINES = 8;

const API_CALL_PATTERNS = [/\bfetch\s*\(/, /\baxios\s*\./, /\baxios\s*\(/];

const ALLOW_INSTANCEOF = "ts-org: allow-instanceof";

const errors = [];

function isComponentFile(filePath) {
  return filePath.endsWith(".tsx");
}

function isIndexFile(filePath) {
  return path.basename(filePath) === "index.ts" || path.basename(filePath) === "index.tsx";
}

function isTestFile(filePath) {
  return (
    filePath.includes("/__tests__/") ||
    filePath.includes(".test.") ||
    filePath.includes(".spec.")
  );
}

function parseFile(filePath, content) {
  try {
    return parse(content, { jsx: true, loc: true, range: false, comment: false });
  } catch {
    return null;
  }
}

function normalizeBody(node, lines) {
  if (!node.body || !node.body.loc) return [];
  const start = node.body.loc.start.line - 1;
  const end = node.body.loc.end.line;
  return lines
    .slice(start, end)
    .map((l) => l.trim().replace(/\s+/g, " "))
    .filter((l) => l && !l.startsWith("//") && !l.startsWith("*"));
}

function extractFunctions(ast, lines) {
  const functions = [];
  function visit(node) {
    if (!node || typeof node !== "object") return;
    const isFn =
      node.type === "FunctionDeclaration" ||
      node.type === "FunctionExpression" ||
      node.type === "ArrowFunctionExpression";
    if (isFn && node.body && node.body.type === "BlockStatement" && node.loc) {
      const name =
        node.id?.name ||
        (node.parent?.type === "VariableDeclarator" ? node.parent.id?.name : null) ||
        "<anonymous>";
      const bodyLines = normalizeBody(node, lines);
      if (bodyLines.length >= MIN_DUPLICATE_BODY_LINES) {
        functions.push({ name, line: node.loc.start.line, key: bodyLines.join("\n") });
      }
    }
    for (const key of Object.keys(node)) {
      if (key === "parent") continue;
      const child = node[key];
      if (Array.isArray(child)) {
        child.forEach((c) => {
          if (c && typeof c === "object" && c.type) {
            c.parent = node;
            visit(c);
          }
        });
      } else if (child && typeof child === "object" && child.type) {
        child.parent = node;
        visit(child);
      }
    }
  }
  visit(ast);
  return functions;
}

function isErrorInstanceofTest(node) {
  if (!node) return false;
  if (node.type === "UnaryExpression" && node.operator === "!") {
    return isErrorInstanceofTest(node.argument);
  }
  return (
    node.type === "BinaryExpression" &&
    node.operator === "instanceof" &&
    node.right?.type === "Identifier" &&
    node.right.name === "Error"
  );
}

// Names a repo might give its "turn an unknown throw into a message" helper.
const ERROR_HELPER_NAMES = ["describeError", "errorMessage", "formatError", "toErrorMessage"];

let errorHelperCache;

/**
 * Find this repo's own error-narrowing helper, so the message points somewhere
 * that exists in the workspace being checked rather than naming loregarden's.
 * Returns `{ name, file }`, or null when the repo has no such helper yet — then
 * the advice is to extract one.
 */
function findErrorHelper(repoRoot) {
  if (errorHelperCache !== undefined) return errorHelperCache;
  const pattern = new RegExp(`export\\s+(?:async\\s+)?function\\s+(${ERROR_HELPER_NAMES.join("|")})\\b`);
  errorHelperCache = null;
  const root = tsSourceRoot(repoRoot);
  const stack = [root];
  while (stack.length > 0 && errorHelperCache === null) {
    const dir = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== "node_modules" && entry.name !== "__tests__") stack.push(full);
      } else if (entry.isFile() && /\.(ts|tsx)$/.test(entry.name) && !isTestFile(full)) {
        let match;
        try {
          match = pattern.exec(fs.readFileSync(full, "utf8"));
        } catch {
          continue;
        }
        if (match) {
          errorHelperCache = { name: match[1], file: path.relative(repoRoot, full) };
          break;
        }
      }
    }
  }
  return errorHelperCache;
}

/**
 * `err instanceof Error ? err.message : "Failed to …"` — the hand-rolled unknown
 * narrowing, copy-pasted ~45 times in loregarden alone. A shared helper says the
 * same thing, and recovers whatever richer error type the ternary throws away.
 *
 * Only the ternary form. A real type guard inside a helper (`if (!(e instanceof
 * Error)) return …`) is how narrowing is supposed to work and stays legal — which
 * is also why the helper's own file is exempt.
 */
function errorNarrowingErrors(filePath, ast, lines, added, repoRoot) {
  if (isTestFile(filePath)) return [];
  const helper = findErrorHelper(repoRoot);
  if (helper && path.resolve(repoRoot, helper.file) === path.resolve(filePath)) return [];
  const advice = helper
    ? `use \`${helper.name}(error, "fallback")\` from ${helper.file}`
    : `extract one shared helper (\`describeError(error, fallback)\`) instead of repeating the ternary`;
  const found = [];
  function visit(node) {
    if (!node || typeof node !== "object") return;
    if (node.type === "ConditionalExpression" && isErrorInstanceofTest(node.test) && node.loc) {
      const lineno = node.loc.start.line;
      const text = lines[lineno - 1] ?? "";
      if (added.has(lineno) && !text.includes(ALLOW_INSTANCEOF)) {
        found.push(`${filePath}:${lineno}: inline \`instanceof Error\` ternary; ${advice}`);
      }
    }
    for (const key of Object.keys(node)) {
      if (key === "parent") continue;
      const child = node[key];
      if (Array.isArray(child)) {
        child.forEach((c) => c && typeof c === "object" && c.type && visit(c));
      } else if (child && typeof child === "object" && child.type) {
        visit(child);
      }
    }
  }
  visit(ast);
  return found;
}

/**
 * A scope git could not resolve — an unresolvable `--base` exits 128 with an
 * empty stdout, indistinguishable from a clean diff. Swallowing it let this
 * gate report a pass over a scope it never resolved; mirrors
 * precommit_git_diff.py's `GitScopeError`.
 */
class GitScopeError extends Error {}

/**
 * Git exports these into hooks and everything they spawn, and they **override
 * `cwd`** — a gate invoked with `--repo <workspace>` from a context that has
 * GIT_DIR set reads the *other* repository, finds nothing, and reports a pass.
 * Mirrors `GIT_LOCATION_ENV_VARS` in precommit_git_diff.py.
 */
const GIT_LOCATION_ENV_VARS = [
  "GIT_DIR",
  "GIT_WORK_TREE",
  "GIT_INDEX_FILE",
  "GIT_OBJECT_DIRECTORY",
  "GIT_COMMON_DIR",
  "GIT_NAMESPACE",
  "GIT_PREFIX",
];

function scrubbedGitEnv() {
  const env = { ...process.env };
  for (const name of GIT_LOCATION_ENV_VARS) delete env[name];
  return env;
}

function git(args, cwd) {
  try {
    return execFileSync("git", args, {
      cwd,
      env: scrubbedGitEnv(),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (err) {
    const detail = String(err.stderr || err.stdout || err.message).trim();
    throw new GitScopeError(`\`git ${args.join(" ")}\` failed in ${cwd}: ${detail}`);
  }
}

/**
 * Refuse a ref git would read as an option — `--base --output=/tmp/x` reached
 * `git diff` as a flag, wrote a file outside the repository, and returned a
 * zero-file exit 0. Mirrors precommit_git_diff.py's `_validated_ref`.
 */
function validatedRef(ref) {
  if (ref.startsWith("-")) {
    throw new GitScopeError(`base ref "${ref}" looks like an option, not a revision`);
  }
  return ref;
}

/**
 * git-diff selector per scope; mirrors precommit_git_diff.py's `_scope_args`.
 * `since` is not a scope a caller may ask for: it is the resolved form of
 * `worktree`, an already-resolved merge base compared against the working tree.
 */
function scopeArgs(diffScope, baseRef) {
  if (diffScope === "worktree") return ["HEAD"];
  if (diffScope === "branch") return [`${validatedRef(baseRef)}...HEAD`];
  if (diffScope === "since") return [validatedRef(baseRef)];
  return ["--cached"];
}

/** The commit this branch forked from, or null when `baseRef` is unknown. */
function mergeBase(repoRoot, baseRef) {
  try {
    return (
      execFileSync("git", ["merge-base", validatedRef(baseRef), "HEAD"], {
        cwd: repoRoot,
        env: scrubbedGitEnv(),
        encoding: "utf8",
        stdio: ["ignore", "pipe", "pipe"],
      }).trim() || null
    );
  } catch (err) {
    if (err instanceof GitScopeError) throw err;
    // Unknown is a real answer: not every workspace calls its trunk `main`, and
    // both callers of this either name the unresolved ref or raise.
    return null;
  }
}

/**
 * False in a repository with no commits yet — an unborn HEAD. `git diff HEAD`
 * cannot resolve there, but a brand-new workspace is not a scope the gate
 * failed to resolve: every file in it is untracked. Mirrors
 * precommit_git_diff.py's `git_has_head`.
 */
function hasHead(repoRoot) {
  try {
    execFileSync("git", ["rev-parse", "--verify", "--quiet", "HEAD"], {
      cwd: repoRoot,
      env: scrubbedGitEnv(),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    return true;
  } catch {
    return false;
  }
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
 *
 * Every path, not just the TypeScript ones — `resolveScope` needs to know
 * whether the *tree* is clean, and a run that only touched Python is not a
 * clean tree.
 */
function changedPaths(repoRoot, diffScope, baseRef) {
  if (diffScope === "worktree" && !hasHead(repoRoot)) {
    return [...new Set(untrackedPaths(repoRoot))];
  }
  const out = git(
    ["diff", ...scopeArgs(diffScope, baseRef), "--name-only", "--diff-filter=ACMR", "--"],
    repoRoot,
  );
  const paths = out.split("\n").map((l) => l.trim()).filter(Boolean);
  if (diffScope === "worktree" || diffScope === "since") paths.push(...untrackedPaths(repoRoot));
  return [...new Set(paths)];
}

/** How a scope reads in the `examined N file(s) — …` line. */
function describeScope(diffScope, baseRef) {
  if (diffScope === "worktree") return "worktree changes vs HEAD";
  if (diffScope === "branch") return `branch diff ${baseRef}...HEAD`;
  if (diffScope === "since") return `worktree and branch changes since ${baseRef}`;
  return "staged changes";
}

/**
 * What this run should examine, and never "nothing" — mirrors
 * precommit_git_diff.py's `resolve_scope`. A driver that commits the ticket
 * worktree to satisfy the worktree-retire guard empties the `HEAD` diff, so
 * `worktree` resolves to the merge base instead: one diff carrying the branch's
 * commits *and* whatever is still uncommitted. The two are not alternatives —
 * consulting the branch only when the whole tree happened to be clean left one
 * stray untracked file able to hide the committed change again.
 */
function resolveScope(repoRoot, diffScope, baseRef) {
  if (diffScope !== "worktree") {
    return {
      diffScope,
      baseRef,
      paths: changedPaths(repoRoot, diffScope, baseRef),
      description: describeScope(diffScope, baseRef),
      includesUntracked: false,
    };
  }
  if (!hasHead(repoRoot)) {
    return {
      diffScope,
      baseRef,
      paths: changedPaths(repoRoot, "worktree", baseRef),
      description: "worktree changes (no commits yet)",
      includesUntracked: true,
    };
  }
  const base = mergeBase(repoRoot, baseRef);
  if (base === null) {
    // Degraded, not resolved: this scope cannot see the branch's commits, so
    // `resolveGateScope` refuses to call it a pass if it grades nothing.
    return {
      diffScope: "worktree",
      baseRef,
      paths: changedPaths(repoRoot, "worktree", baseRef),
      description:
        `worktree changes vs HEAD (base "${baseRef}" did not resolve; ` +
        "branch commits not examined)",
      includesUntracked: true,
      degraded: true,
    };
  }
  return {
    diffScope: "since",
    baseRef: base,
    paths: changedPaths(repoRoot, "since", base),
    description: describeScope("since", baseRef),
    includesUntracked: true,
  };
}

/**
 * Resolve, filter, count, announce and diff — the whole preamble, once, so no
 * gate can omit one of the steps and report a pass over files it never read.
 * The returned `touched(filePath, lineCount)` answers with the *resolved*
 * scope: a gate reaching for the requested one instead prints a credible file
 * count over an empty touched-line set. Mirrors precommit_git_diff.py's
 * `resolve_gate_scope` / `GateRun`.
 */
function resolveGateScope({ label, repoRoot, diffScope, baseRef, files, select }) {
  // An explicit file list (lefthook) means the caller already scoped this run.
  const scope =
    files.length > 0
      ? {
          diffScope,
          baseRef,
          paths: [],
          description: describeScope(diffScope, baseRef),
          includesUntracked: false,
          degraded: false,
        }
      : resolveScope(repoRoot, diffScope, baseRef);
  const discovered = files.length === 0;
  const candidates = discovered ? scope.paths.map((rel) => path.resolve(repoRoot, rel)) : files;
  const graded = select(candidates, discovered);
  if (scope.degraded && graded.length === 0) {
    // The fallback is only tolerable while it still has something to grade.
    // With nothing left, this run read none of the branch's commits and none of
    // the working tree either: exiting 0 would report a pass over a scope that
    // was never resolved.
    throw new GitScopeError(
      `base ref "${scope.baseRef}" did not resolve, so this run fell back to ` +
        "worktree changes vs HEAD and found nothing to grade: the branch's " +
        "commits went unread",
    );
  }
  // Counted after filtering, always: the number has to be the number of files
  // the gate read, or it is one more thing that looks like a pass over work.
  console.log(`${label}: examined ${graded.length} file(s) — ${scope.description}`);
  const untracked = new Set(
    scope.includesUntracked && graded.length > 0
      ? untrackedPaths(repoRoot).map((p) => path.resolve(repoRoot, p))
      : [],
  );
  const touched = (filePath, lineCount) =>
    stagedAdditions(
      path.relative(repoRoot, path.resolve(filePath)),
      repoRoot,
      scope.diffScope,
      scope.baseRef,
      untracked.has(path.resolve(filePath)),
      lineCount,
    );
  return { scope, files: graded, touched };
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

function checkFile(filePath, content, lines, { added, netGrowing, repoRoot }) {
  const fileErrors = [];
  const lineCount = lines.length;

  if (isIndexFile(filePath)) {
    if (lineCount > MAX_INDEX_LINES && netGrowing) {
      fileErrors.push(
        `${filePath}: index file is ${lineCount} lines (max ${MAX_INDEX_LINES}); keep barrel files minimal (re-exports only)`,
      );
    }
  } else {
    const maxLines = isComponentFile(filePath) ? MAX_TSX_FILE_LINES : MAX_FILE_LINES;
    if (lineCount > maxLines && netGrowing) {
      fileErrors.push(
        `${filePath}: file is ${lineCount} lines (max ${maxLines}); split into smaller modules`,
      );
    }
  }

  if (isComponentFile(filePath) && !isTestFile(filePath)) {
    lines.forEach((line, idx) => {
      const lineno = idx + 1;
      if (!added.has(lineno)) return;
      for (const pattern of API_CALL_PATTERNS) {
        if (pattern.test(line)) {
          fileErrors.push(
            `${filePath}:${lineno}: direct API call in component; move to a custom hook (useXxx) or service module`,
          );
          break;
        }
      }
    });
  }

  const ast = parseFile(filePath, content);
  if (ast) {
    fileErrors.push(...errorNarrowingErrors(filePath, ast, lines, added, repoRoot));
    const functions = extractFunctions(ast, lines);
    const seen = new Map();
    for (const fn of functions) {
      if (seen.has(fn.key)) {
        const first = seen.get(fn.key);
        fileErrors.push(
          `${filePath}: duplicated function bodies detected (${first.name}@${first.line}, ${fn.name}@${fn.line}); extract shared helper to keep DRY`,
        );
      } else {
        seen.set(fn.key, fn);
      }
    }
  }

  return fileErrors;
}

/**
 * Where this repo keeps its TypeScript. loregarden uses client/src; other
 * workspaces put it at src/ or app/. Detected, not hardcoded, because these
 * checks run against every workspace the control plane drives.
 */
function tsSourceRoot(repoRoot) {
  for (const candidate of ["client/src", "src", "app", "frontend/src"]) {
    const full = path.resolve(repoRoot, candidate);
    if (fs.existsSync(full)) return full;
  }
  return path.resolve(repoRoot);
}

function buildCatalog(changedSet, repoRoot) {
  const catalog = new Map();
  const clientSrc = tsSourceRoot(repoRoot);
  if (!fs.existsSync(clientSrc)) return catalog;

  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === "node_modules" || entry.name === "__tests__") continue;
        walk(full);
      } else if (entry.isFile() && /\.(ts|tsx)$/.test(entry.name)) {
        if (changedSet.has(full)) continue;
        if (isTestFile(full)) continue;
        try {
          const content = fs.readFileSync(full, "utf8");
          const lines = content.split("\n");
          const ast = parseFile(full, content);
          if (!ast) continue;
          for (const fn of extractFunctions(ast, lines)) {
            if (!catalog.has(fn.key)) catalog.set(fn.key, []);
            catalog.get(fn.key).push({ file: full, name: fn.name, line: fn.line });
          }
        } catch {
          // skip
        }
      }
    }
  }
  walk(clientSrc);
  return catalog;
}

function crossDryErrors(filePath, content, lines, catalog) {
  const fileErrors = [];
  const ast = parseFile(filePath, content);
  if (!ast) return fileErrors;
  for (const fn of extractFunctions(ast, lines)) {
    const matches = catalog.get(fn.key);
    if (!matches || matches.length === 0) continue;
    const refs = matches
      .slice(0, 3)
      .map((m) => `${path.relative(process.cwd(), m.file)}:${m.name}@${m.line}`)
      .join(", ");
    fileErrors.push(
      `${filePath}:${fn.line}: function \`${fn.name}\` duplicates existing code (${refs}); reuse existing logic to keep DRY`,
    );
  }
  return fileErrors;
}

function parseArgv(argv) {
  const files = [];
  let repoArg = null;
  let diffScope = "staged";
  let baseRef = "main";
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--repo" && argv[i + 1]) repoArg = argv[(i += 1)];
    else if (argv[i] === "--scope" && argv[i + 1]) diffScope = argv[(i += 1)];
    else if (argv[i] === "--base" && argv[i + 1]) baseRef = argv[(i += 1)];
    else if (/\.(ts|tsx)$/.test(argv[i])) files.push(argv[i]);
  }
  if (!["staged", "worktree", "branch"].includes(diffScope)) diffScope = "staged";
  const repoRoot = repoArg ? path.resolve(repoArg) : process.cwd();
  const label = diffScope === "staged" && !repoArg ? "pre-commit" : "gate";
  return { files, repoRoot, diffScope, baseRef, label };
}

/**
 * The TypeScript files this gate grades. A `discovered` list came from a diff,
 * so it is confined to the repo's own source root — mirroring the lefthook
 * glob, so the gate does not judge build tooling by rules written for
 * application code. An explicit list was already scoped by its caller, and
 * narrowing it again would silently drop files that caller meant to have
 * graded.
 */
function tsFilesInScope(repoRoot, candidates, discovered) {
  const sourceRoot = tsSourceRoot(repoRoot);
  return candidates.filter(
    (candidate) =>
      /\.(ts|tsx)$/.test(candidate) &&
      (!discovered || path.resolve(repoRoot, candidate).startsWith(`${sourceRoot}${path.sep}`)),
  );
}

function run({ files, repoRoot, diffScope, baseRef, label }) {
  const { files: args, touched } = resolveGateScope({
    label,
    repoRoot,
    diffScope,
    baseRef,
    files,
    select: (candidates, discovered) => tsFilesInScope(repoRoot, candidates, discovered),
  });
  if (args.length === 0) {
    return 0;
  }

  const changedSet = new Set(args.map((a) => path.resolve(a)));
  const catalog = buildCatalog(changedSet, repoRoot);

  for (const filePath of args) {
    if (!fs.existsSync(filePath)) continue;
    const content = fs.readFileSync(filePath, "utf8");
    const lines = content.split("\n");
    const { added, addedCount, deletedCount } = touched(filePath, lines.length);
    const netGrowing = addedCount > deletedCount;
    errors.push(...checkFile(filePath, content, lines, { added, netGrowing, repoRoot }));
    if (!isTestFile(filePath)) {
      errors.push(...crossDryErrors(filePath, content, lines, catalog));
    }
  }

  if (errors.length > 0) {
    console.error(`${label}: TypeScript organization check failed:`);
    for (const err of errors) {
      console.error(` - ${err}`);
    }
    return 1;
  }

  console.log(`${label}: TypeScript organization checks passed.`);
  return 0;
}

const invocation = parseArgv(process.argv.slice(2));
try {
  process.exit(run(invocation));
} catch (err) {
  if (!(err instanceof GitScopeError)) throw err;
  // A scope the gate could not resolve is not a scope it examined.
  console.error(`${invocation.label}: cannot determine what to examine: ${err.message}`);
  process.exit(1);
}
