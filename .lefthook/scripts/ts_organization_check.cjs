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
 * A scope git could not resolve — an unresolvable `--base` exits 128 with an
 * empty stdout, indistinguishable from a clean diff. Swallowing it let this
 * gate report a pass over a scope it never resolved; mirrors
 * precommit_git_diff.py's `GitScopeError`.
 */
class GitScopeError extends UnexaminableError {}

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
 * One git-printed path token as a real path; mirrors `decode_git_path` in
 * precommit_git_diff.py, and see that docstring for why decoding beats `-z`.
 *
 * `core.quotePath` is git's default, so `src/bäd.ts` arrives from `--name-only`
 * as `"src/b\303\244d.ts"`. Consumed raw it is not a path: it fails the
 * `\.tsx?$` filter and is dropped, which made the *printed file count* wrong —
 * a two-file diff announced `examined 1 file(s)` and passed over the one it
 * never saw.
 */
function decodeGitPath(token) {
  if (token.length < 2 || !token.startsWith('"') || !token.endsWith('"')) return token;
  const body = token.slice(1, -1);
  const bytes = [];
  let i = 0;
  while (i < body.length) {
    const char = body[i];
    if (char !== "\\") {
      bytes.push(...Buffer.from(char, "utf8"));
      i += 1;
      continue;
    }
    i += 1;
    const escape = body[i];
    if (escape === undefined) {
      throw new GitScopeError(`git printed a malformed quoted path: ${token}`);
    }
    if (Object.prototype.hasOwnProperty.call(C_ESCAPES, escape)) {
      bytes.push(C_ESCAPES[escape]);
      i += 1;
      continue;
    }
    const octal = body.slice(i, i + 3);
    if (!/^[0-7]{3}$/.test(octal)) {
      throw new GitScopeError(`git printed a malformed quoted path: ${token}`);
    }
    bytes.push(parseInt(octal, 8));
    i += 3;
  }
  return Buffer.from(bytes).toString("utf8");
}

/**
 * Every path in a git command's path-per-line output, decoded. No `.trim()`:
 * git quotes anything that would make a line ambiguous, so what is left is
 * literal, and trimming corrupted a path with a trailing space.
 */
function decodedGitPaths(out) {
  return out.split("\n").filter(Boolean).map(decodeGitPath);
}

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
  try {
    return fs.readFileSync(real, "utf8");
  } catch (err) {
    throw new UnexaminableFileError(
      `${filePath}: this run could not read it, so it cannot be reported clean (${err.message})`,
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

/** Scopes a caller may ask for; mirrors precommit_git_diff.py's `DIFF_SCOPES`. */
const DIFF_SCOPES = ["staged", "worktree", "branch"];
/** Scopes whose file list must include untracked files; mirrors `_UNTRACKED_SCOPES`. */
const UNTRACKED_SCOPES = ["worktree", "since"];

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

function scrubbedGitEnv() {
  const env = { ...process.env };
  for (const name of GIT_LOCATION_ENV_VARS) delete env[name];
  for (const name of Object.keys(env)) {
    if (GIT_CONFIG_ENV_PREFIXES.some((prefix) => name.startsWith(prefix))) delete env[name];
  }
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

/** The base a gate falls back to when its caller named none. */
const DEFAULT_BASE_REF = "main";

/** Trunk names tried when `DEFAULT_BASE_REF` names nothing; `origin/HEAD` first. */
const TRUNK_REF_CANDIDATES = ["master", "trunk", "develop"];

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
 * True when `ref` names a commit. Separates the two failures `git merge-base`
 * reports with the same exit status — a ref that does not exist, and a ref that
 * exists but shares no history with HEAD. Mirrors precommit_git_diff.py's
 * `git_rev_exists`.
 */
function revExists(repoRoot, ref) {
  try {
    execFileSync("git", ["rev-parse", "--verify", "--quiet", `${validatedRef(ref)}^{commit}`], {
      cwd: repoRoot,
      env: scrubbedGitEnv(),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    return true;
  } catch (err) {
    if (err instanceof GitScopeError) throw err;
    return false;
  }
}

/** The trunk `origin/HEAD` names (e.g. `origin/master`), or null. */
function originHead(repoRoot) {
  try {
    return (
      execFileSync("git", ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], {
        cwd: repoRoot,
        env: scrubbedGitEnv(),
        encoding: "utf8",
        stdio: ["ignore", "pipe", "pipe"],
      }).trim() || null
    );
  } catch (err) {
    if (err instanceof GitScopeError) throw err;
    return null;
  }
}

/**
 * `baseRef`, or this repository's actual trunk when nobody named one. Mirrors
 * precommit_git_diff.py's `effective_base_ref`: a repository on `master` — what
 * a bare `git init` still produces wherever `init.defaultBranch` is unset —
 * resolved the default to nothing, degraded, and then failed every stage
 * transition over a trunk name rather than over any code. Only the default is
 * substituted; an explicit `--base` gets that ref or a loud failure.
 */
function effectiveBaseRef(repoRoot, baseRef) {
  if (baseRef !== DEFAULT_BASE_REF || revExists(repoRoot, baseRef)) return baseRef;
  for (const candidate of [originHead(repoRoot), ...TRUNK_REF_CANDIDATES]) {
    if (candidate && candidate !== baseRef && revExists(repoRoot, candidate)) return candidate;
  }
  return baseRef;
}

/**
 * The empty tree's hash as this repository spells it; diffing against it yields
 * everything, which is the branch diff of a branch sharing no commit with its
 * base. Mirrors precommit_git_diff.py's `git_empty_tree`.
 */
function emptyTree(repoRoot) {
  return git(["hash-object", "-t", "tree", "/dev/null"], repoRoot).trim();
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
  return decodedGitPaths(git(["ls-files", "--others", "--exclude-standard"], repoRoot));
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
  const paths = decodedGitPaths(out);
  if (UNTRACKED_SCOPES.includes(diffScope)) paths.push(...untrackedPaths(repoRoot));
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
function resolveScope(repoRoot, diffScope, requestedBaseRef) {
  const baseRef = effectiveBaseRef(repoRoot, requestedBaseRef);
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
  if (base === null && revExists(repoRoot, baseRef)) {
    // The ref resolves; the histories are disjoint (orphan branch, re-inited
    // repo, shallow clone). Every commit on this branch is unshared, so the
    // branch diff is the whole tree. Refusing to run here blocked every stage
    // transition in such a workspace. Mirrors precommit_git_diff.py.
    const root = emptyTree(repoRoot);
    return {
      diffScope: "since",
      baseRef: root,
      paths: changedPaths(repoRoot, "since", root),
      description: `whole branch and worktree (no common ancestor with "${baseRef}")`,
      includesUntracked: true,
    };
  }
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
  if (!DIFF_SCOPES.includes(diffScope)) {
    // Coercing an unrecognised `--scope` to `staged` made a typo examine the
    // index — empty at a stage transition — and exit 0 over a committed
    // violation. Mirrors precommit_git_diff.py's `resolve_gate_scope`.
    throw new GitScopeError(
      `unknown scope "${diffScope}"; expected one of ${DIFF_SCOPES.join(", ")}`,
    );
  }
  // An explicit file list (lefthook) means the caller already scoped this run.
  const scope =
    files.length > 0
      ? {
          diffScope,
          baseRef,
          paths: [],
          description: describeScope(diffScope, baseRef),
          // Derived, not assumed: an explicitly named untracked file under
          // `--scope worktree` has no diff to read, and calling it tracked left
          // its whole contents ungraded while Python graded them.
          includesUntracked: UNTRACKED_SCOPES.includes(diffScope),
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
  if (discovered && hasHead(repoRoot)) announceUngradedSubmodules(label, repoRoot, scope);
  const untracked = new Set(
    scope.includesUntracked && graded.length > 0
      ? untrackedPaths(repoRoot).map((p) => path.resolve(repoRoot, p))
      : [],
  );
  const undiffable =
    graded.length > 0
      ? undiffablePaths(repoRoot, scope.diffScope, scope.baseRef)
      : new Set();
  const touched = (filePath, lineCount) => {
    const abs = path.resolve(filePath);
    // Changed, but git would not say where: there is no smaller honest answer
    // than the whole file, and the empty set is what let `-diff` pass everything.
    if (undiffable.has(abs) && !untracked.has(abs)) return wholeFile(lineCount);
    return stagedAdditions(
      path.relative(repoRoot, abs),
      repoRoot,
      scope.diffScope,
      scope.baseRef,
      untracked.has(abs),
      lineCount,
    );
  };
  return { scope, files: graded, touched };
}

/** `git diff --raw` spells a gitlink — a submodule pointer — with this mode. */
const GITLINK_MODE = "160000";

/**
 * Say out loud that a submodule bump went ungraded.
 *
 * `--name-only` lists the gitlink like any other path, the language filter
 * drops it (it is a directory), and the run prints `examined 0 file(s)` and
 * exits 0 over a change nobody read. A gate cannot grade another repository,
 * and failing on every pointer move would block transitions for reasons
 * unrelated to code quality — but silently exiting 0 is the one option this
 * ticket exists to remove. Mirrors `announce_ungraded_submodules`.
 */
function announceUngradedSubmodules(label, repoRoot, scope) {
  const out = git(
    ["diff", ...scopeArgs(scope.diffScope, scope.baseRef), "--raw", "--"],
    repoRoot,
  );
  const found = new Set();
  for (const line of out.split("\n")) {
    if (!line.startsWith(":")) continue;
    const [head, ...rest] = line.split("\t");
    if (rest.length === 0) continue;
    const modes = head.slice(1).split(/\s+/).slice(0, 2);
    if (!modes.includes(GITLINK_MODE)) continue;
    found.add(decodeGitPath(rest[rest.length - 1]));
  }
  if (found.size === 0) return;
  const names = [...found].sort();
  console.log(
    `${label}: not examined — ${names.length} submodule(s) changed ` +
      `(${names.join(", ")}); gate their own repository there`,
  );
}

function wholeFile(lineCount) {
  const added = new Set();
  for (let i = 1; i <= lineCount; i += 1) added.add(i);
  return { added, addedCount: lineCount, deletedCount: 0 };
}

/**
 * Absolute paths git reports as changed but produces no hunk for — graded whole.
 *
 * Two ways in, one meaning. `--numstat` writes `-\t-\tpath` for a diff git
 * itself declines: a real binary, or a path a `.gitattributes` entry marks
 * `-diff`/`binary`. But a `diff=<driver>` naming a command that prints nothing,
 * and a `filter=` that cleans a file to empty, suppress the diff with real
 * counts still reported — the marker never fires, `stagedAdditions` fell
 * through its `if (!diff)` to an empty added-set, and the gate printed a
 * plausible count and a pass over a committed violation. So the diff text is
 * asked directly: *did you emit a `+++ b/<path>` header for this path*. That
 * question has one answer for every way of suppressing a diff, including the
 * next one.
 *
 * Non-zero counts are the discriminator: a mode-only `chmod` reports `0\t0` and
 * legitimately has no hunk, so it stays out. Mirrors `DiffNumstat.undiffable`
 * plus `suppressed_diff_paths` in precommit_git_diff.py; two git calls for the
 * whole run.
 */
function undiffablePaths(repoRoot, diffScope, baseRef) {
  const out = git(["diff", ...scopeArgs(diffScope, baseRef), "--numstat", "--"], repoRoot);
  const suppressed = new Set();
  const counted = new Map();
  for (const line of out.split("\n")) {
    const parts = line.split("\t");
    if (parts.length !== 3) continue;
    const rel = decodeGitPath(parts[2]);
    if (parts[0] === "-" || parts[1] === "-") {
      suppressed.add(path.resolve(repoRoot, rel));
      continue;
    }
    // The added column alone. The rule below compares it against additions the
    // diff actually described, and a deletion has none to describe.
    counted.set(rel, Number(parts[0]));
  }
  const diff = git(
    ["diff", ...scopeArgs(diffScope, baseRef), "--no-color", "-U0", "--"],
    repoRoot,
  );
  // Additions per path, from the one diff — the parity of
  // `precommit_git_diff.parse_staged_additions`. Counting them is what replaced
  // asking whether a header was printed: a driver that emits the three header
  // lines and exits satisfied that question and described nothing (576).
  const parsedAdded = new Map();
  let current = null;
  for (const line of diff.split("\n")) {
    if (line.startsWith("+++ ")) {
      const name = decodeGitPath(line.slice(4));
      current = name.startsWith("b/") ? name.slice(2) : null;
      if (current !== null && !parsedAdded.has(current)) parsedAdded.set(current, 0);
      continue;
    }
    if (current !== null && line.startsWith("+") && !line.startsWith("+++")) {
      parsedAdded.set(current, parsedAdded.get(current) + 1);
    }
  }
  for (const [rel, addedCount] of counted) {
    // git counted N additions; the diff described M. Short means the diff did
    // not describe the change, whatever it printed.
    if ((parsedAdded.get(rel) ?? 0) < addedCount) suppressed.add(path.resolve(repoRoot, rel));
  }
  // A path this scope *adds* is new in its entirety, exactly like an untracked
  // one. Normally a no-op — every line of an added file is a `+` line anyway —
  // which is why it is safe, and why it is asked separately: a `filter=` whose
  // clean step empties the blob commits the file with no content, so `--numstat`
  // says `0\t0` and the diff carries no hunk while the file on disk (the one
  // this gate reads) is full of code. Counts alone cannot tell that from a
  // mode-only `chmod`; "it is new, so all of it is new" can. Mirrors
  // `git_added_paths` in precommit_git_diff.py.
  const added = git(
    ["diff", ...scopeArgs(diffScope, baseRef), "--name-only", "--diff-filter=A", "--"],
    repoRoot,
  );
  for (const rel of decodedGitPaths(added)) suppressed.add(path.resolve(repoRoot, rel));
  return suppressed;
}

function stagedAdditions(repoRel, repoRoot, diffScope, baseRef, isUntracked, lineCount) {
  if (isUntracked) {
    // Nothing to diff against: the whole file is new.
    return wholeFile(lineCount);
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

  const ast = parseGradedFile(filePath, content);
  {
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
    const content = readSource(filePath, repoRoot);
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
  if (!(err instanceof UnexaminableError)) throw err;
  // One handler for the one invariant: a scope this run could not resolve, and
  // a file it could not read, are both things it did not examine.
  console.error(`${invocation.label}: cannot determine what to examine: ${err.message}`);
  process.exit(1);
}
