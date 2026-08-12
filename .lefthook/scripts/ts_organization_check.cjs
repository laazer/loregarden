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

function git(args, cwd) {
  try {
    return execFileSync("git", args, {
      cwd,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch {
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
  const paths = out.split("\n").map((l) => l.trim()).filter(Boolean);
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

const { files, repoRoot, diffScope, baseRef, label } = parseArgv(process.argv.slice(2));

// Gate mode: no explicit list, so the diff says what to read — confined to the
// repo's TypeScript source root, mirroring the lefthook glob.
const sourceRoot = tsSourceRoot(repoRoot);
const args =
  files.length > 0
    ? files
    : changedPaths(repoRoot, diffScope, baseRef)
        .map((rel) => path.resolve(repoRoot, rel))
        .filter((full) => full.startsWith(`${sourceRoot}${path.sep}`));

if (args.length === 0) {
  process.exit(0);
}

const untracked = new Set(
  diffScope === "worktree" ? untrackedPaths(repoRoot).map((p) => path.resolve(repoRoot, p)) : [],
);
const changedSet = new Set(args.map((a) => path.resolve(a)));
const catalog = buildCatalog(changedSet, repoRoot);

for (const filePath of args) {
  if (!fs.existsSync(filePath)) continue;
  const content = fs.readFileSync(filePath, "utf8");
  const lines = content.split("\n");
  const repoRel = path.relative(repoRoot, path.resolve(filePath));
  const { added, addedCount, deletedCount } = stagedAdditions(
    repoRel,
    repoRoot,
    diffScope,
    baseRef,
    untracked.has(path.resolve(filePath)),
    lines.length,
  );
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
  process.exit(1);
}

console.log(`${label}: TypeScript organization checks passed.`);
process.exit(0);
