#!/usr/bin/env node
/**
 * Keep frontend failures visible to the person using the app.
 *
 * The app already has the surfacing path: `request()` throws `ApiError`,
 * `describeError()` turns any thrown thing into a sentence (recovering the
 * status line an inline ternary discards), and `ToastHost` renders it. What is
 * missing is any enforcement that a failure reaches it. A `catch {}` compiles,
 * an empty list renders, and the user reads "no data" where the truth was "the
 * request failed".
 *
 * oxlint cannot do this job: `no-empty` is not enabled, and
 * `no-floating-promises` / `no-misused-promises` are type-aware
 * `@typescript-eslint` rules oxlint does not implement.
 *
 * Checks (diff-scoped, on .ts/.tsx):
 *   1. catch block that neither rethrows, nor records state, nor surfaces
 *   2. catch whose only statement is a console.* call
 *   3. `.catch(() => {})` / `.catch(console.error)` — a rejection discarded
 *   4. Promise.all/allSettled over raw fetch with no `.ok` check in the file:
 *      a 500 resolves, so "0 failed" is reported when everything failed
 *
 * A suppression that is genuinely fine says so on the line, with a reason:
 *
 *     } catch { /* silent-ok: probe only; the poll re-checks in 2s *\/ }
 *
 * The marker alone is not enough — the reason must be substantive.
 */

const fs = require("fs");
const path = require("path");
const { createRequire } = require("module");

const clientRoot = path.resolve(__dirname, "../../client");
const requireFromClient = createRequire(path.join(clientRoot, "package.json"));
const { parse } = requireFromClient("@typescript-eslint/typescript-estree");

const {
  untrackedPaths,
  stagedAdditions,
  isTestFile,
  parseArgv,
  filesToCheck,
} = require("./ts_git_diff.cjs");

const ALLOW_MARKER = "silent-ok:";
const MIN_WAIVER_REASON_CHARS = 12;

// Names that mean the failure reached a human or a rendered surface.
const SURFACING_CALLEES = new Set([
  "pushToast",
  "toastActionFailed",
  "toastWarning",
  "describeError",
  "errorDetail",
  "pushInboxNotification",
  "captureException",
]);

const errors = [];

function parseFile(content) {
  try {
    return parse(content, { jsx: true, loc: true, range: false, comment: false });
  } catch {
    // silent-ok: tsc and oxlint both report syntax errors, and far better
    return null;
  }
}

function waiverReason(line) {
  const at = line.indexOf(ALLOW_MARKER);
  if (at === -1) return null;
  return line
    .slice(at + ALLOW_MARKER.length)
    .replace(/\*\/\s*\}?\s*$/, "")
    .trim();
}

/** Walk up over the comment block above, where a multi-line reason lives. */
function waiverStart(lines, start) {
  let first = start;
  while (first > 1) {
    const above = lines[first - 2].trim();
    if (!above.startsWith("//") && !above.startsWith("/*") && !above.startsWith("*")) break;
    first -= 1;
  }
  return first;
}

function spanWaived(lines, start, end) {
  for (let i = waiverStart(lines, start); i <= Math.min(end, lines.length); i += 1) {
    const reason = waiverReason(lines[i - 1]);
    if (reason !== null && reason.length >= MIN_WAIVER_REASON_CHARS) return true;
  }
  return false;
}

function shortWaiverLine(lines, start, end) {
  for (let i = waiverStart(lines, start); i <= Math.min(end, lines.length); i += 1) {
    const reason = waiverReason(lines[i - 1]);
    if (reason !== null && reason.length < MIN_WAIVER_REASON_CHARS) return i;
  }
  return null;
}

function spanTouched(added, start, end) {
  if (added === null) return true;
  for (let i = start; i <= end; i += 1) if (added.has(i)) return true;
  return false;
}

function walk(node, visit, parent = null) {
  if (!node || typeof node.type !== "string") return;
  visit(node, parent);
  for (const key of Object.keys(node)) {
    if (key === "parent" || key === "loc") continue;
    const value = node[key];
    if (Array.isArray(value)) {
      for (const child of value) {
        if (child && typeof child.type === "string") walk(child, visit, node);
      }
    } else if (value && typeof value.type === "string") {
      walk(value, visit, node);
    }
  }
}

function calleeName(node) {
  if (!node || node.type !== "CallExpression") return null;
  const callee = node.callee;
  if (!callee) return null;
  if (callee.type === "Identifier") return callee.name;
  if (callee.type === "MemberExpression" && callee.property) {
    return callee.property.name ?? null;
  }
  return null;
}

function isConsoleCall(node) {
  return (
    node &&
    node.type === "CallExpression" &&
    node.callee &&
    node.callee.type === "MemberExpression" &&
    node.callee.object &&
    node.callee.object.type === "Identifier" &&
    node.callee.object.name === "console"
  );
}

/** What a catch block does about the error it caught. */
function observeCatch(handler) {
  const seen = {
    rethrows: false,
    consoleOnly: false,
    surfaces: false,
    recordsState: false,
    statements: 0,
  };
  let nonConsoleCalls = 0;
  let consoleCalls = 0;

  walk(handler.body, (node) => {
    if (node.type === "ThrowStatement") seen.rethrows = true;
    else if (node.type === "CallExpression") {
      if (isConsoleCall(node)) {
        consoleCalls += 1;
        return;
      }
      nonConsoleCalls += 1;
      const name = calleeName(node);
      if (name && SURFACING_CALLEES.has(name)) seen.surfaces = true;
      // A setter is how a component records a failure it will render:
      // setError(...), setBootError(...), setSaveError(...).
      if (name && /^set[A-Z]/.test(name)) seen.recordsState = true;
    } else if (node.type === "AssignmentExpression" || node.type === "VariableDeclarator") {
      seen.recordsState = true;
    } else if (node.type === "ReturnStatement" && node.argument) {
      // Returning something built from the error hands it to the caller.
      seen.recordsState = true;
    }
  });

  seen.statements = (handler.body.body || []).length;
  seen.consoleOnly = consoleCalls > 0 && nonConsoleCalls === 0 && !seen.rethrows;
  return seen;
}

function catchErrors(filePath, ast, lines, added) {
  const found = [];
  walk(ast, (node) => {
    if (node.type !== "CatchClause") return;
    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return;

    const short = shortWaiverLine(lines, start, end);
    if (short !== null) {
      found.push(
        `${filePath}:${short}: '${ALLOW_MARKER}' with no real reason; say why the failure is ` +
          `safe to swallow (transient, retryable, or expected with a surfaced alternate path), ` +
          `in at least ${MIN_WAIVER_REASON_CHARS} characters`,
      );
      return;
    }
    if (spanWaived(lines, start, end)) return;

    const seen = observeCatch(node);
    if (seen.rethrows || seen.surfaces || seen.recordsState) return;

    if (seen.statements === 0) {
      found.push(
        `${filePath}:${start}: empty catch block — the failure is invisible to the user; ` +
          `surface it via pushToast/describeError, record it in state you render, rethrow, ` +
          `or waive with '${ALLOW_MARKER} <reason>'`,
      );
      return;
    }
    if (seen.consoleOnly) {
      found.push(
        `${filePath}:${start}: catch only writes to the console, which no user sees; ` +
          `surface it via pushToast/describeError or record it in state you render`,
      );
      return;
    }
    found.push(
      `${filePath}:${start}: catch neither rethrows nor surfaces the failure; ` +
        `pushToast/describeError, record it in rendered state, rethrow, or waive with ` +
        `'${ALLOW_MARKER} <reason>'`,
    );
  });
  return found;
}

/** `.catch(() => {})` and `.catch(console.error)` — a rejection thrown away. */
function discardedRejectionErrors(filePath, ast, lines, added) {
  const found = [];
  walk(ast, (node) => {
    if (node.type !== "CallExpression") return;
    if (calleeName(node) !== "catch" || node.arguments.length !== 1) return;
    const arg = node.arguments[0];
    const line = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, line, end)) return;
    if (spanWaived(lines, line, end)) return;

    const isFn =
      arg.type === "ArrowFunctionExpression" || arg.type === "FunctionExpression";
    // `() => {}` and `() => undefined` are the same discard written two ways;
    // the second is an expression body, not an empty block.
    const isConstantDiscard =
      isFn &&
      arg.body.type !== "BlockStatement" &&
      ((arg.body.type === "Identifier" && arg.body.name === "undefined") ||
        (arg.body.type === "Literal" && (arg.body.value === null || arg.body.value === false)) ||
        (arg.body.type === "UnaryExpression" && arg.body.operator === "void"));
    const isEmptyFn =
      (isFn && arg.body.type === "BlockStatement" && arg.body.body.length === 0) ||
      isConstantDiscard;
    const isConsoleRef =
      arg.type === "MemberExpression" &&
      arg.object &&
      arg.object.type === "Identifier" &&
      arg.object.name === "console";

    if (isEmptyFn) {
      found.push(
        `${filePath}:${line}: the .catch handler discards the rejection (() => {} / ` +
          `() => undefined); surface it via pushToast/describeError, record it in rendered ` +
          `state, or waive with '${ALLOW_MARKER} <reason>'`,
      );
    } else if (isConsoleRef) {
      found.push(
        `${filePath}:${line}: .catch(console.*) reports where no user looks; surface it via ` +
          `pushToast/describeError`,
      );
    }
  });
  return found;
}

/**
 * `Promise.allSettled(ids.map((id) => fetch(...)))` then counting `rejected`.
 * fetch resolves for 4xx/5xx, so a bulk action reports zero failures when the
 * server rejected every one of them.
 */
function settledFetchErrors(filePath, ast, lines, added) {
  const found = [];
  walk(ast, (node) => {
    if (node.type !== "CallExpression") return;
    const name = calleeName(node);
    if (name !== "allSettled" && name !== "all") return;
    const callee = node.callee;
    if (
      !callee ||
      callee.type !== "MemberExpression" ||
      !callee.object ||
      callee.object.name !== "Promise"
    ) {
      return;
    }
    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return;
    if (spanWaived(lines, start, end)) return;

    let callsFetch = false;
    let checksOk = false;
    walk(node, (inner) => {
      if (inner.type === "CallExpression" && calleeName(inner) === "fetch") callsFetch = true;
      if (inner.type === "MemberExpression" && inner.property && inner.property.name === "ok") {
        checksOk = true;
      }
    });
    if (callsFetch && !checksOk) {
      found.push(
        `${filePath}:${start}: Promise.${name} over raw fetch without checking response.ok — ` +
          `fetch resolves for 4xx/5xx, so server-side failures are counted as successes; ` +
          `check .ok per response`,
      );
    }
  });
  return found;
}

const invocation = parseArgv(process.argv.slice(2));
const { repoRoot, diffScope, baseRef, label, scanAll } = invocation;
const args = filesToCheck(invocation);

if (args.length === 0) {
  process.exit(0);
}

const untracked = new Set(
  diffScope === "worktree" ? untrackedPaths(repoRoot).map((p) => path.resolve(repoRoot, p)) : [],
);

for (const filePath of args) {
  if (!fs.existsSync(filePath) || isTestFile(filePath)) continue;
  const content = fs.readFileSync(filePath, "utf8");
  const ast = parseFile(content);
  if (!ast) continue;
  const lines = content.split("\n");
  const repoRel = path.relative(repoRoot, path.resolve(filePath));
  // `--all` ignores diff scoping entirely: null means "every line counts".
  const added = scanAll
    ? null
    : stagedAdditions(
        repoRel,
        repoRoot,
        diffScope,
        baseRef,
        untracked.has(path.resolve(filePath)),
        lines.length,
      ).added;
  errors.push(...catchErrors(filePath, ast, lines, added));
  errors.push(...discardedRejectionErrors(filePath, ast, lines, added));
  errors.push(...settledFetchErrors(filePath, ast, lines, added));
}

if (errors.length > 0) {
  console.error(`${label}: silent failure check failed:`);
  for (const err of errors) {
    console.error(` - ${err}`);
  }
  process.exit(1);
}

console.log(`${label}: silent failure checks passed.`);
process.exit(0);
