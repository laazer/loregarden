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
const { spawnSync } = require("child_process");

const clientRoot = path.resolve(__dirname, "../../client");
const requireFromClient = createRequire(path.join(clientRoot, "package.json"));
const { parse } = requireFromClient("@typescript-eslint/typescript-estree");

const MAX_FILE_LINES = 1200;
const MAX_TSX_FILE_LINES = 1200;
const MAX_INDEX_LINES = 80;
const MIN_DUPLICATE_BODY_LINES = 8;

const API_CALL_PATTERNS = [/\bfetch\s*\(/, /\baxios\s*\./, /\baxios\s*\(/];

const ALLOW_INSTANCEOF = "ts-org: allow-instanceof";

/** Where a TypeScript source tree lives, most specific first. This gate's own
 *  policy; the resolver turns it into a path against the repository root. */
const TS_SOURCE_ROOT_CANDIDATES = ["client/src", "src", "app", "frontend/src"];

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
function findErrorHelper(sourceRoot) {
  if (errorHelperCache !== undefined) return errorHelperCache;
  const pattern = new RegExp(`export\\s+(?:async\\s+)?function\\s+(${ERROR_HELPER_NAMES.join("|")})\\b`);
  errorHelperCache = null;
  if (sourceRoot === null) return errorHelperCache;
  const stack = [sourceRoot];
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
          errorHelperCache = { name: match[1], file: path.relative(sourceRoot, full) };
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
function errorNarrowingErrors(filePath, ast, lines, added, sourceRoot) {
  if (isTestFile(filePath)) return [];
  const helper = findErrorHelper(sourceRoot);
  if (helper && path.resolve(sourceRoot, helper.file) === path.resolve(filePath)) return [];
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
 * This run could not examine something it was asked to grade.
 *
 * **The invariant of every gate here: a gate may not report success over
 * anything it did not actually read.** Both ways of failing it live under this
 * one type and leave through one handler, so a third way inherits the
 * behaviour instead of becoming the next silent pass. Mirrors
 * `UnexaminableError` in precommit_git_diff.py.
 */
class UnexaminableError extends Error {}

/**
 * A file this run was told to grade but could not read or parse — missing (a
 * cone sparse-checkout, a `skip-worktree` entry whose file was removed),
 * unreadable, or unparseable. `existsSync` + `continue` treated every one of
 * those exactly like a file that graded clean: `examined 1 file(s)` +
 * `checks passed.` + exit 0, over a violation sitting in the commit.
 * Mirrors `UnexaminableFileError` in precommit_git_diff.py.
 */
class UnexaminableFileError extends UnexaminableError {}

/** git's C-quoting escapes, as `quote_c_style` writes them. */
const C_ESCAPES = { a: 7, b: 8, f: 12, n: 10, r: 13, t: 9, v: 11, "\\": 92, '"': 34 };

/**
 * A source file larger than this is not graded. Mirrors `MAX_SOURCE_BYTES` in
 * precommit_git_diff.py: no hand-written module comes near it, and a path that
 * does is a device, a stream, or a mistake.
 */
const MAX_SOURCE_BYTES = 8 * 1024 * 1024;

/**
 * The text of a file this gate is about to grade, or a loud failure. Never
 * null: the caller cannot tell "nothing wrong here" from "I never read it",
 * and it took the first reading every time.
 *
 * The same rule as `read_source_text` in precommit_git_diff.py, in the same
 * order: resolve, refuse a non-regular target, refuse a target that leaves the
 * repository, cap the size, then read. A bare `readFileSync` follows a
 * committed `src/x.ts -> /dev/zero` until the host gives out, and reads and
 * reports on a file the repository does not contain — one gate refusing that
 * while its mirror does not is a rule that exists only in one language.
 *
 * The boundary is crossed only when the *listed* path is inside `repoRoot` and
 * its target is not; a path the caller named outright scoped the run itself.
 * Both sides are real-pathed, or a checkout behind a symlinked prefix (macOS
 * `/var` -> `/private/var`) has the check silently skipped for every file.
 */
function readSource(filePath, repoRoot) {
  let real;
  let stat;
  try {
    real = fs.realpathSync(filePath);
    stat = fs.statSync(real);
  } catch (err) {
    throw new UnexaminableFileError(
      `${filePath}: this run could not read it, so it cannot be reported clean (${err.message})`,
    );
  }
  if (!stat.isFile()) {
    throw new UnexaminableFileError(
      `${filePath}: not a regular file (resolves to ${real}), so it cannot be graded and cannot be reported clean`,
    );
  }
  if (repoRoot) {
    const root = fs.realpathSync(repoRoot);
    // The listed path with its *directory* resolved but not the file itself —
    // `locatedPath` in precommit_git_diff.py, and for the same reason.
    const located = path.join(fs.realpathSync(path.dirname(filePath)), path.basename(filePath));
    // Component containment, not `startsWith`: `/w/repo` is a string prefix of
    // `/w/repo-vendor/x.ts`, which is where vendored trees sit.
    const inside = (candidate) => candidate === root || candidate.startsWith(root + path.sep);
    if (inside(located) && !inside(real)) {
      throw new UnexaminableFileError(
        `${filePath}: resolves to ${real}, outside the repository at ${root}, so it cannot be graded and cannot be reported clean`,
      );
    }
  }
  if (stat.size > MAX_SOURCE_BYTES) {
    throw new UnexaminableFileError(
      `${filePath}: ${stat.size} bytes exceeds the ${MAX_SOURCE_BYTES}-byte grading limit (resolves to ${real}), so it cannot be graded and cannot be reported clean`,
    );
  }
  let bytes;
  try {
    bytes = fs.readFileSync(real);
  } catch (err) {
    throw new UnexaminableFileError(
      `${filePath}: this run could not read it, so it cannot be reported clean (${err.message})`,
    );
  }
  try {
    // `readFileSync(..., "utf8")` does not throw on invalid UTF-8 — it
    // substitutes U+FFFD — so the gate parsed mangled text and could report it
    // clean. Python refuses the same file with UnicodeDecodeError; this is that
    // rule, in the half of the mirror that could not express it (594).
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (err) {
    throw new UnexaminableFileError(
      `${filePath}: is not valid UTF-8 (resolves to ${real}), so it cannot be graded ` +
        `and cannot be reported clean (${err.message})`,
    );
  }
}

/** The AST of a file this gate grades. A file it cannot parse is not a file it cleared. */
function parseGradedFile(filePath, content) {
  const ast = parseFile(filePath, content);
  if (ast === null) {
    throw new UnexaminableFileError(
      `${filePath}: this run could not parse it, so it cannot be reported clean`,
    );
  }
  return ast;
}


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
  "GIT_ALTERNATE_OBJECT_DIRECTORIES",
  "GIT_CONFIG_COUNT",
];

/**
 * Ad-hoc config git reads from `GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>` pairs,
 * counted by `GIT_CONFIG_COUNT`. One pair setting `core.attributesFile` can mark
 * sources `-diff`, emptying a diff while `--name-only` still lists the file.
 * Mirrors `GIT_CONFIG_ENV_PREFIXES` in precommit_git_diff.py.
 */
const GIT_CONFIG_ENV_PREFIXES = ["GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"];

/** The base a gate falls back to when its caller named none. */
const DEFAULT_BASE_REF = "main";

/** Trunk names tried when `DEFAULT_BASE_REF` names nothing; `origin/HEAD` first. */
const TRUNK_REF_CANDIDATES = ["master", "trunk", "develop"];

/**
 * Resolve, filter, count, announce and diff — the whole preamble, once, so no
 * gate can omit one of the steps and report a pass over files it never read.
 * The returned `touched(filePath, lineCount)` answers with the *resolved*
 * scope: a gate reaching for the requested one instead prints a credible file
 * count over an empty touched-line set. Mirrors precommit_git_diff.py's
 * `resolve_gate_scope` / `GateRun`.
 */
/**
 * Ask `precommit_git_diff.py` what this run should examine.
 *
 * This used to be ~560 lines of hand-ported Python living in this file: the
 * error classes, git-path decoding, env scrubbing, ref validation, scope
 * resolution, untracked discovery, submodule announcement and diff-suppression
 * detection. None of it was TypeScript-specific, and three of one review's nine
 * defects were the two copies drifting apart — each fix having to be written
 * twice, in two languages, by whoever remembered the other existed (580).
 *
 * The gate already shells out to git repeatedly. One more subprocess buys a
 * single implementation of scope policy, so a scope fix now lands in Python and
 * both languages get it.
 *
 * What stays on this side is the part that is genuinely TypeScript's: which
 * suffixes to grade and which source root to confine discovery to. Those are
 * passed *in*, so the file count is still computed after this gate's own filter
 * — by the same code that counts for the Python gates.
 *
 * Run through `server_python.sh` rather than a bare `python3`: the gate scripts
 * need 3.11 and refuse (exit 69) on anything older rather than grading (657).
 */
function resolveGateScope({ label, repoRoot, diffScope, baseRef, files }) {
  const scriptDir = __dirname;
  const emitted = spawnSync(
    "bash",
    [
      path.join(scriptDir, "server_python.sh"),
      path.join(scriptDir, "precommit_git_diff.py"),
      "--emit-scope-json",
      ...(repoRoot === null ? [] : ["--repo", repoRoot]),
      "--scope",
      diffScope,
      "--base",
      baseRef,
      "--label",
      label,
      "--suffix",
      ".ts",
      "--suffix",
      ".tsx",
      // The candidate list is this gate's policy; resolving it is not. Passing
      // names rather than an absolute path removes the chicken-and-egg where
      // the source root had to be computed from a repository root only the
      // resolver knows (594).
      ...TS_SOURCE_ROOT_CANDIDATES.flatMap((dir) => ["--select-root-candidate", dir]),
      ...files.map((f) => path.resolve(f)),
    ],
    { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 },
  );

  if (emitted.error) {
    throw new UnexaminableError(`could not run the scope resolver: ${emitted.error.message}`);
  }
  let payload;
  try {
    payload = JSON.parse(emitted.stdout);
  } catch (parseError) {
    // Anything that is not JSON means the resolver did not get far enough to
    // answer — a missing interpreter, an import failure, a crash. None of those
    // are "nothing to examine", so none of them may become a pass.
    throw new UnexaminableError(
      `the scope resolver produced no usable answer (exit ${emitted.status}): ` +
        `${(emitted.stderr || emitted.stdout || "").trim().split("\n").slice(-3).join(" ")}`,
    );
  }
  for (const notice of payload.notices || []) console.log(notice);
  if (payload.error) throw new UnexaminableError(payload.error);

  // Everything below measures against the root the resolver used, not the
  // one this process guessed.
  const resolvedRoot = payload.repo_root ?? repoRoot ?? null;
  // No repository at all — a bare file list outside one. Python skips the
  // containment check in exactly that case, so relpaths become absolute rather
  // than this process crashing on `path.relative(null, ...)`, which would fail
  // the run for a reason unrelated to what it examined.
  const relOf = (filePath) =>
    resolvedRoot === null
      ? path.resolve(filePath)
      : path.relative(resolvedRoot, path.resolve(filePath));
  const untracked = new Set(payload.untracked || []);
  const undiffable = new Set(payload.undiffable || []);
  const additions = payload.additions || {};
  const counts = payload.counts || {};

  const touched = (filePath, lineCount) => {
    const rel = relOf(filePath);
    // Untracked, or changed in a way git would not describe: there is no
    // smaller honest answer than the whole file, and the empty set is what let
    // `-diff` pass everything.
    if (untracked.has(rel) || undiffable.has(rel)) return wholeFile(lineCount);
    const [addedCount = 0, deletedCount = 0] = counts[rel] || [];
    return { added: new Set(additions[rel] || []), addedCount, deletedCount };
  };

  return {
    scope: payload.scope,
    files: payload.files,
    touched,
    repoRoot: resolvedRoot,
    sourceRoot: payload.select_root ?? null,
  };
}

function wholeFile(lineCount) {
  const added = new Set();
  for (let i = 1; i <= lineCount; i += 1) added.add(i);
  return { added, addedCount: lineCount, deletedCount: 0 };
}

function checkFile(filePath, content, lines, { added, netGrowing, sourceRoot }) {
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

  const ast = parseGradedFile(filePath, content);
  {
    fileErrors.push(...errorNarrowingErrors(filePath, ast, lines, added, sourceRoot));
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
function buildCatalog(changedSet, sourceRoot) {
  const catalog = new Map();
  const clientSrc = sourceRoot;
  if (clientSrc === null || !fs.existsSync(clientSrc)) return catalog;

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
        } catch (err) {
          // Background for the DRY catalog, not a file this run grades, so it
          // cannot make the run report a violation clean. It can still weaken a
          // DRY match, so it is reported rather than dropped.
          console.error(
            `note: catalog skipped an unreadable file, so DRY matches may be incomplete: ${full}: ${err.message}`,
          );
        }
      }
    }
  }
  walk(clientSrc);
  return catalog;
}

function crossDryErrors(filePath, content, lines, catalog) {
  const fileErrors = [];
  const ast = parseGradedFile(filePath, content);
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
  let baseRef = DEFAULT_BASE_REF;
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--repo" && argv[i + 1]) repoArg = argv[(i += 1)];
    else if (argv[i] === "--scope" && argv[i + 1]) diffScope = argv[(i += 1)];
    else if (argv[i] === "--base" && argv[i + 1]) baseRef = argv[(i += 1)];
    else if (/\.(ts|tsx)$/.test(argv[i])) files.push(argv[i]);
  }
  // Left null when unset: the scope resolver answers with the git-derived
  // root, so this gate and the Python gates agree on what "the repository" is
  // regardless of where the caller stood (594).
  const repoRoot = repoArg ? path.resolve(repoArg) : null;
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
function run({ files, repoRoot: requestedRoot, diffScope, baseRef, label }) {
  // `repoRoot` below is the one the resolver settled on, not the one this
  // process asked for — those differ whenever `--repo` was omitted (594).
  const {
    files: args,
    touched,
    repoRoot,
    sourceRoot,
  } = resolveGateScope({
    label,
    repoRoot: requestedRoot,
    diffScope,
    baseRef,
    files,
  });
  if (args.length === 0) {
    return 0;
  }

  const changedSet = new Set(args.map((a) => path.resolve(a)));
  const catalog = buildCatalog(changedSet, sourceRoot);

  for (const filePath of args) {
    const content = readSource(filePath, repoRoot);
    const lines = content.split("\n");
    const { added, addedCount, deletedCount } = touched(filePath, lines.length);
    const netGrowing = addedCount > deletedCount;
    errors.push(...checkFile(filePath, content, lines, { added, netGrowing, sourceRoot }));
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
  if (!(err instanceof UnexaminableError)) throw err;
  // One handler for the one invariant: a scope this run could not resolve, and
  // a file it could not read, are both things it did not examine.
  console.error(`${invocation.label}: cannot determine what to examine: ${err.message}`);
  process.exit(1);
}
