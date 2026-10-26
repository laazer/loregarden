#!/usr/bin/env node
/**
 * Keep font sizes on the type scale.
 *
 * Type had the same failure as colour and motion: every surface picked its own.
 * The client carried 37 distinct font sizes, 85% of them at 13px or below and
 * some at 8px, in half-pixel steps nobody can tell apart. The scale is six
 * tokens on `:root` in client/src/index.css (`--fs-micro` 11px up to
 * `--fs-display` 24px); this gate holds new lines to them.
 *
 * Checks (diff-scoped, on .ts/.tsx/.css under the detected TypeScript root, and
 * only where the root stylesheet defines the `--fs-*` tokens):
 *   1. A `font-size` or `font` declaration in a stylesheet whose size is an
 *      absolute length (px, rem, pt), outside a theme block (`:root`,
 *      `[data-theme…]`), where the tokens are defined. Relative sizes (em, %,
 *      smaller, inherit) and sizes computed from a variable are left alone.
 *   2. A `fontSize` property in code given a number or an absolute length.
 *
 * A case that is genuinely right says so on the line, with a reason:
 *
 *     font-size: 34px; /* type-ok: the Home greeting, the one display size above the scale *\/
 *     fontSize: 9, // type-ok: Pixi TextStyle on the Hive canvas; CSS tokens cannot reach it
 */

const fs = require("fs");
const path = require("path");

const { requireClientModule } = require("./gate_client_modules.cjs");
const { parseFile, walk, literalText, propertyKey } = require("./gate_jsx.cjs");
const {
  untrackedPaths,
  stagedAdditions,
  isTestFile,
  parseArgv,
  filesToCheck,
  tsSourceRoot,
} = require("./ts_git_diff.cjs");
const { waiversFor, spanTouched, shortWaiverErrors } = require("./gate_waivers.cjs");

const ALLOW_MARKER = "type-ok:";
const SOURCE = /\.(ts|tsx|css)$/;
const ROOT_STYLESHEET = "index.css";
const THEME_SELECTOR = /:root\b|\[data-theme\b/;

// Ceiling (px) and token for each step, smallest first. Matches :root.
const STEPS = [
  [11.75, "--fs-micro"],
  [12.75, "--fs-small"],
  [14.25, "--fs-body"],
  [17, "--fs-lead"],
  [21.5, "--fs-title"],
  [Infinity, "--fs-display"],
];
const PX_PER = { px: 1, rem: 16, pt: 4 / 3 };
const ABSOLUTE_LENGTH = /(?<![\w.-])(\d*\.?\d+)(px|rem|pt)(?![\w-])/i;
// In the `font` shorthand the size is the first length; a line height follows a slash.
const FONT_SHORTHAND_SIZE = /^(?:\s*(?:italic|oblique|normal|small-caps|bold|bolder|lighter|\d{3})\s+)*\s*(\d*\.?\d+)(px|rem|pt)\b/i;

const errors = [];

function nearestToken(px) {
  return STEPS.find(([ceiling]) => px <= ceiling)[1];
}

function message(filePath, line, where, amount, unit) {
  const px = Number(amount) * PX_PER[unit.toLowerCase()];
  return (
    `${filePath}:${line}: ${where} hardcodes '${amount}${unit}' — use var(${nearestToken(px)}) ` +
    `(micro 11: badges and mono metadata · small 12: secondary text, dense tables · body 13 · ` +
    `lead 15: card and section titles · title 19: page titles · display 24)`
  );
}

function rootDefinesScale(repoRoot) {
  const file = path.join(tsSourceRoot(repoRoot), ROOT_STYLESHEET);
  return fs.existsSync(file) && /--fs-body\s*:/.test(fs.readFileSync(file, "utf8"));
}

// --------------------------------------------------------------------------- //
// CSS
// --------------------------------------------------------------------------- //

function inThemeBlock(node) {
  for (let parent = node.parent; parent; parent = parent.parent) {
    if (parent.type === "rule" && THEME_SELECTOR.test(parent.selector)) return true;
  }
  return false;
}

function cssErrors(filePath, content, added, waivers) {
  let root;
  try {
    root = requireClientModule("postcss").parse(content, { from: filePath });
  } catch (err) {
    // Not a silent skip: a stylesheet this gate cannot read is one it did not check.
    return [
      `${filePath}: could not parse this stylesheet, so its font sizes were not checked: ` +
        `${err.reason ?? err.message}`,
    ];
  }

  const found = [];
  root.walkDecls((decl) => {
    if (inThemeBlock(decl)) return;
    let match = null;
    if (decl.prop === "font-size") match = ABSOLUTE_LENGTH.exec(decl.value);
    else if (decl.prop === "font") match = FONT_SHORTHAND_SIZE.exec(decl.value);
    if (!match) return;
    const start = decl.source.start.line;
    const end = decl.source.end ? decl.source.end.line : start;
    if (!spanTouched(added, start, end) || waivers.waived(start, end)) return;
    found.push(message(filePath, start, `'${decl.prop}'`, match[1], match[2]));
  });
  return found;
}

// --------------------------------------------------------------------------- //
// TS / TSX
// --------------------------------------------------------------------------- //

function codeErrors(filePath, ast, added, waivers) {
  const found = [];
  walk(ast, (node) => {
    if (node.type !== "Property" || propertyKey(node) !== "fontSize") return;
    let amount = null;
    let unit = "px";
    if (node.value.type === "Literal" && typeof node.value.value === "number") {
      amount = String(node.value.value);
    } else {
      const match = ABSOLUTE_LENGTH.exec(literalText(node.value) ?? "");
      if (match) [, amount, unit] = match;
    }
    if (amount === null) return;
    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end) || waivers.waived(start, end)) return;
    found.push(message(filePath, start, "fontSize", amount, unit));
  });
  return found;
}

// --------------------------------------------------------------------------- //

const invocation = parseArgv(process.argv.slice(2), SOURCE);
const { repoRoot, diffScope, baseRef, label, scanAll } = invocation;
const args = filesToCheck(invocation);

if (args.length === 0) {
  process.exit(0);
}
// Load the toolchain before deciding there is nothing to check, so a missing one
// reports itself as unavailable (exit 69) instead of reading as a pass.
requireClientModule("postcss");
// Asking for var(--fs-body) in a repo that defines no scale would be a finding with no fix.
if (!rootDefinesScale(repoRoot)) {
  process.exit(0);
}

const untracked = new Set(
  diffScope === "worktree" ? untrackedPaths(repoRoot).map((p) => path.resolve(repoRoot, p)) : [],
);

for (const filePath of args) {
  if (isTestFile(filePath)) continue;
  const content = fs.readFileSync(filePath, "utf8");
  const lines = content.split("\n");
  const waivers = waiversFor(ALLOW_MARKER, lines);
  const absolute = path.resolve(filePath);
  const repoRel = path.relative(repoRoot, absolute);
  // `--all` ignores diff scoping entirely: null means "every line counts".
  const added = scanAll
    ? null
    : stagedAdditions(repoRel, repoRoot, diffScope, baseRef, untracked.has(absolute), lines.length)
        .added;

  if (filePath.endsWith(".css")) {
    errors.push(...cssErrors(filePath, content, added, waivers));
  } else {
    const ast = parseFile(content);
    if (!ast) continue;
    errors.push(...codeErrors(filePath, ast, added, waivers));
  }
  errors.push(
    ...shortWaiverErrors(ALLOW_MARKER, filePath, lines, added, waivers, "say why this size is off the scale"),
  );
}

if (errors.length > 0) {
  console.error(`${label}: type scale check failed:`);
  for (const err of errors) {
    console.error(` - ${err}`);
  }
  process.exit(1);
}

console.log(`${label}: type scale checks passed.`);
process.exit(0);
