#!/usr/bin/env node
/**
 * Keep the app usable by the person in front of it.
 *
 * The silent-failure gate covers the failure path: an error the user never
 * sees. This one covers the three states next to it that are just as invisible
 * in review and just as loud in use — a control nothing can name, a control a
 * keyboard cannot reach, and a list that renders nothing and explains nothing.
 *
 * None of these are caught by anything already running here. oxlint's config
 * enables no jsx-a11y rules (they are a separate plugin and are not on), tsc
 * has no opinion about an accessible name, and a screenshot review sees an icon
 * button and reads it as fine because the reviewer already knows what it does.
 *
 * Checks (diff-scoped, on .ts/.tsx under the detected TypeScript root):
 *   1. <button>/<a> whose only children are icons — no text, no aria-label,
 *      no title. Nothing announces it, and nothing explains it on hover.
 *   2. onClick on a non-interactive element without role + tabIndex + a key
 *      handler. Reachable with a mouse, unreachable without one.
 *   3. A dismiss backdrop — role="presentation" with onClick — in a file that
 *      never handles Escape. The mouse can close it and the keyboard cannot,
 *      and the focus trap deliberately left this one open (useDialogFocusTrap:
 *      "the twenty-one that do not close on Escape are a separate decision").
 *   4. A fetched list rendered with .map() in a file that never handles the
 *      empty case. Zero rows and a blank pane are the same picture, and the
 *      user cannot tell "none yet" from "it broke".
 *
 * A case that is genuinely fine says so on the line, with a reason:
 *
 *     <div onClick={close} /> {/* ux-ok: backdrop; Esc closes and the dialog traps focus *\/}
 *
 * The marker alone is not enough — the reason must be substantive, the same
 * contract `silent-ok:` carries.
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

const ALLOW_MARKER = "ux-ok:";
const MIN_WAIVER_REASON_CHARS = 12;

// Elements the browser already makes focusable, clickable and announceable.
// `label` and `option` are here because a click on them is handled by the
// control they belong to, not by them.
const NATIVELY_INTERACTIVE = new Set([
  "a",
  "button",
  "input",
  "label",
  "option",
  "select",
  "summary",
  "textarea",
]);

// Attributes that give an element an accessible name.
const NAMING_ATTRS = new Set(["aria-label", "aria-labelledby", "title"]);

// An element hidden from the accessibility tree on purpose. A modal backdrop is
// the case here: it is `role="presentation"` precisely so it is not announced or
// tabbed to, so demanding tabIndex on it would be asking for the opposite of
// what it is for. Its keyboard obligation is Escape, checked separately.
const PRESENTATIONAL_ROLES = new Set(["presentation", "none"]);

// Structure, not a control. A `role="dialog"` panel carrying
// `onClick={(e) => e.stopPropagation()}` is stopping the backdrop from closing
// underneath it, and asking that container for a key handler is asking it to be
// something it is not. The widget roles — button, link, tab, menuitem — are
// deliberately absent: those genuinely owe the keyboard an answer.
const CONTAINER_ROLES = new Set([
  "alertdialog",
  "dialog",
  "document",
  "group",
  "list",
  "listitem",
  "region",
  "toolbar",
]);

// Handling a key press by name. Any of these means the file thought about the
// keyboard path out; which key object it reads from is not this gate's business.
const ESCAPE_PATTERNS = [/["'`]Escape["'`]/, /\bkeyCode\s*===?\s*27\b/, /\buseDialogDismiss\b/];

// Any of these appearing in a file is a handled empty case. Deliberately broad:
// the gate's job is to notice a list nobody thought about, not to dictate how
// the empty state is written.
const EMPTY_STATE_PATTERNS = [
  // `?.` is written as often as `.` here, and a check the gate cannot see is a
  // finding it invents: `if (!findings?.length) return null` is a handled empty
  // case that the un-optional form of this pattern read as an unhandled one.
  /\??\.length\s*===?\s*0/,
  /\??\.length\s*(?:\?|>|&&)/,
  /!\w[\w.?]*\??\.length/,
  /\blength\s*<\s*1\b/,
  /\bisEmpty\b/,
  /\bEmpty(?:State|List|Message|Placeholder)\b/,
  /\bemptyLabel\b/,
  /\bnoResults\b/i,
];

// A file with none of these is not rendering fetched data, so a .map() in it is
// over something the author wrote by hand and cannot surprise anyone by being
// empty.
const ASYNC_SOURCE_PATTERNS = [
  /\bawait\b/,
  /\bfetch\(/,
  /\buseQuery\b/,
  /\buseSWR\b/,
  /\buseEffect\b/,
  /\buse[A-Z]\w*Store\b/,
  /\brequest\(/,
];

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
    .replace(/(?:\*\/|\}|\s)*$/, "")
    .trim();
}

/**
 * 1-based line numbers that are entirely comment.
 *
 * Computed by a forward pass rather than by per-line prefix matching, because
 * in JSX the only way to comment above an element is `{/* … *\/}`, whose
 * continuation lines start with ordinary prose. The prefix test read the last
 * line of such a block as code and stopped the walk there, so a multi-line JSX
 * waiver silently did not apply — the failure mode a waiver exists to avoid.
 */
function commentLines(lines) {
  const inComment = new Set();
  let open = false;
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const trimmed = line.trim();
    if (open) {
      inComment.add(i + 1);
      if (line.includes("*/")) open = false;
      continue;
    }
    const blockStart = trimmed.indexOf("/*");
    if (blockStart !== -1 && (trimmed.startsWith("/*") || trimmed.startsWith("{/*"))) {
      inComment.add(i + 1);
      // A block that opens and closes on one line leaves nothing open.
      if (!line.includes("*/", line.indexOf("/*") + 2)) open = true;
      continue;
    }
    if (trimmed.startsWith("//")) inComment.add(i + 1);
  }
  return inComment;
}

/** Walk up over the comment block above, where a multi-line reason lives. */
function waiverStart(lines, start, comments) {
  let first = start;
  while (first > 1 && comments.has(first - 1)) first -= 1;
  return first;
}

function spanWaived(lines, start, end, comments) {
  for (let i = waiverStart(lines, start, comments); i <= Math.min(end, lines.length); i += 1) {
    const reason = waiverReason(lines[i - 1]);
    if (reason !== null && reason.length >= MIN_WAIVER_REASON_CHARS) return true;
  }
  return false;
}

function shortWaiverLine(lines, start, end, comments) {
  for (let i = waiverStart(lines, start, comments); i <= Math.min(end, lines.length); i += 1) {
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

function walk(node, visit) {
  if (!node || typeof node.type !== "string") return;
  visit(node);
  for (const key of Object.keys(node)) {
    if (key === "parent" || key === "loc") continue;
    const value = node[key];
    if (Array.isArray(value)) {
      for (const child of value) walk(child, visit);
    } else if (value && typeof value.type === "string") {
      walk(value, visit);
    }
  }
}

/** The tag as written: "div", "Dialog.Panel", or "" for something dynamic. */
function elementName(opening) {
  const name = opening && opening.name;
  if (!name) return "";
  if (name.type === "JSXIdentifier") return name.name;
  if (name.type === "JSXMemberExpression") {
    return `${elementNameOf(name.object)}.${name.property.name}`;
  }
  return "";
}

function elementNameOf(node) {
  if (!node) return "";
  if (node.type === "JSXIdentifier") return node.name;
  if (node.type === "JSXMemberExpression") {
    return `${elementNameOf(node.object)}.${node.property.name}`;
  }
  return "";
}

/** An intrinsic element is lowercase; a component is not, and we cannot see inside it. */
function isIntrinsic(name) {
  return name !== "" && name[0] === name[0].toLowerCase() && !name.includes(".");
}

/** The literal string value of one attribute, or "" when it is not a literal. */
function attributeValue(opening, wanted) {
  for (const attr of opening.attributes || []) {
    if (attr.type === "JSXSpreadAttribute") continue;
    if (!attr.name || attr.name.name !== wanted) continue;
    const value = attr.value;
    if (value && value.type === "Literal" && typeof value.value === "string") return value.value;
    return "";
  }
  return "";
}

function attributeNames(opening) {
  const names = new Set();
  let hasSpread = false;
  for (const attr of opening.attributes || []) {
    if (attr.type === "JSXSpreadAttribute") {
      hasSpread = true;
      continue;
    }
    const name = attr.name;
    if (!name) continue;
    if (name.type === "JSXIdentifier") names.add(name.name);
    else if (name.type === "JSXNamespacedName") {
      names.add(`${name.namespace.name}:${name.name.name}`);
    }
  }
  return { names, hasSpread };
}

/**
 * Whether the element carries its own visible or announced text.
 *
 * A `{expr}` child returns null, not false: the expression may well be a label,
 * and a gate that cannot tell must not accuse.
 */
function hasOwnText(element) {
  let sawExpression = false;
  let sawText = false;
  for (const child of element.children || []) {
    if (child.type === "JSXText" && child.value.trim() !== "") sawText = true;
    else if (child.type === "JSXExpressionContainer") sawExpression = true;
    else if (child.type === "JSXElement" || child.type === "JSXFragment") {
      const nested = hasOwnText(child);
      if (nested === null) sawExpression = true;
      else if (nested) sawText = true;
    }
  }
  if (sawText) return true;
  return sawExpression ? null : false;
}

/** 1. A control with no name at all — not spoken, not hoverable, not guessable. */
function unnamedControlErrors(filePath, ast, lines, added, comments) {
  const found = [];
  walk(ast, (node) => {
    if (node.type !== "JSXElement") return;
    const name = elementName(node.openingElement);
    if (name !== "button" && name !== "a") return;

    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return;
    if (spanWaived(lines, start, end, comments)) return;

    const { names, hasSpread } = attributeNames(node.openingElement);
    // Props may arrive wholesale, and dangerouslySetInnerHTML has children we
    // cannot read. Either way the name may be there; do not guess.
    if (hasSpread || names.has("dangerouslySetInnerHTML")) return;
    if ([...NAMING_ATTRS].some((attr) => names.has(attr))) return;

    const text = hasOwnText(node);
    if (text === true || text === null) return;

    found.push(
      `${filePath}:${start}: <${name}> has no text and no aria-label/title — a screen ` +
        `reader announces it as "button", and a hover explains nothing; add an aria-label ` +
        `naming the action`,
    );
  });
  return found;
}

/** 2. Clickable to a mouse, invisible to a keyboard. */
function keyboardUnreachableErrors(filePath, ast, lines, added, comments) {
  const found = [];
  walk(ast, (node) => {
    if (node.type !== "JSXOpeningElement") return;
    const name = elementName(node);
    if (!isIntrinsic(name) || NATIVELY_INTERACTIVE.has(name)) return;

    const { names, hasSpread } = attributeNames(node);
    if (!names.has("onClick") || hasSpread) return;

    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return;
    if (spanWaived(lines, start, end, comments)) return;

    const role = attributeValue(node, "role");
    // A backdrop is presentational by design; its obligation is Escape, below.
    if (PRESENTATIONAL_ROLES.has(role) || CONTAINER_ROLES.has(role)) return;

    const missing = [];
    if (!names.has("role")) missing.push("role");
    if (!names.has("tabIndex")) missing.push("tabIndex");
    if (!["onKeyDown", "onKeyUp", "onKeyPress"].some((k) => names.has(k))) {
      missing.push("a key handler");
    }
    if (missing.length === 0) return;

    found.push(
      `${filePath}:${start}: <${name} onClick> is missing ${missing.join(", ")} — a keyboard ` +
        `user cannot reach or fire it; use <button> for an action, or add role, tabIndex ` +
        `and onKeyDown`,
    );
  });
  return found;
}

/** Identifiers bound to an array literal in this file — handwritten, never surprising. */
function literalArrayNames(ast) {
  const names = new Set();
  walk(ast, (node) => {
    if (node.type !== "VariableDeclarator") return;
    if (!node.id || node.id.type !== "Identifier") return;
    if (node.init && node.init.type === "ArrayExpression") names.add(node.id.name);
  });
  return names;
}

/** The identifier a `.map()` is called on: `rows` in `rows.map`, `a.b` in `a.b.map`. */
function mappedBaseName(callee) {
  let object = callee.object;
  while (object && object.type === "MemberExpression") object = object.object;
  return object && object.type === "Identifier" ? object.name : "";
}

/**
 * 3. Dismissable by mouse, not by keyboard.
 *
 * A backdrop wired to `onClick={onClose}` is the app saying this thing is
 * dismissable. Without an Escape handler that promise holds for a pointer and
 * breaks for everything else — and once a focus trap is in place, a keyboard
 * operator is not merely inconvenienced, they are held inside a dialog with no
 * way out that does not involve a mouse.
 */
function backdropWithoutEscapeErrors(filePath, ast, lines, added, comments, content) {
  if (ESCAPE_PATTERNS.some((re) => re.test(content))) return [];

  const found = [];
  walk(ast, (node) => {
    if (node.type !== "JSXOpeningElement") return;
    if (!PRESENTATIONAL_ROLES.has(attributeValue(node, "role"))) return;
    const { names } = attributeNames(node);
    if (!names.has("onClick")) return;

    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return;
    if (spanWaived(lines, start, end, comments)) return;

    found.push(
      `${filePath}:${start}: this backdrop closes on click, but nothing in this file ` +
        `handles Escape — a keyboard operator inside the focus trap has no way out; ` +
        `close on Escape as well`,
    );
  });
  return found;
}

/** 4. A fetched list whose empty case nothing renders. */
function missingEmptyStateErrors(filePath, ast, lines, added, comments, content) {
  if (!ASYNC_SOURCE_PATTERNS.some((re) => re.test(content))) return [];
  if (EMPTY_STATE_PATTERNS.some((re) => re.test(content))) return [];

  const literals = literalArrayNames(ast);
  const found = [];
  const reported = new Set();
  walk(ast, (node) => {
    if (node.type !== "CallExpression") return;
    const callee = node.callee;
    if (!callee || callee.type !== "MemberExpression") return;
    if (!callee.property || callee.property.name !== "map") return;

    // Only a map that renders. A map producing values is not an empty state.
    const body = node.arguments[0];
    if (!body || (body.type !== "ArrowFunctionExpression" && body.type !== "FunctionExpression")) {
      return;
    }
    let rendersJsx = false;
    let rendersOptions = false;
    walk(body, (inner) => {
      if (inner.type === "JSXElement" || inner.type === "JSXFragment") rendersJsx = true;
      if (inner.type === "JSXOpeningElement" && elementName(inner) === "option") {
        rendersOptions = true;
      }
    });
    if (!rendersJsx) return;
    // A `<select>` with no options is a disabled picker, not a blank pane. The
    // control still renders and still says what it is.
    if (rendersOptions) return;

    const base = mappedBaseName(callee);
    // A literal array in this file, or an imported SCREAMING_SNAKE constant:
    // both are lists the author wrote out, and neither can arrive empty.
    if (base && (literals.has(base) || /^[A-Z0-9_]+$/.test(base))) return;

    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return;
    if (spanWaived(lines, start, end, comments)) return;
    if (reported.has(start)) return;
    reported.add(start);

    found.push(
      `${filePath}:${start}: ${base || "this list"}.map() renders rows, but nothing in this ` +
        `file handles the empty case — zero rows and a failed load look identical to the ` +
        `user; render an empty state, or waive with 'ux-ok:' if a parent renders it`,
    );
  });
  return found;
}

/** A waiver too thin to have been thought about is itself the finding. */
function shortWaiverErrors(filePath, lines, added, comments) {
  const found = [];
  for (let i = 1; i <= lines.length; i += 1) {
    if (!spanTouched(added, i, i)) continue;
    const line = shortWaiverLine(lines, i, i, comments);
    if (line !== null) {
      found.push(
        `${filePath}:${line}: 'ux-ok:' with no substantive reason — say why this is right ` +
          `for the person using it, in at least ${MIN_WAIVER_REASON_CHARS} characters`,
      );
    }
  }
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
  const comments = commentLines(lines);
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
  errors.push(...unnamedControlErrors(filePath, ast, lines, added, comments));
  errors.push(...keyboardUnreachableErrors(filePath, ast, lines, added, comments));
  errors.push(...backdropWithoutEscapeErrors(filePath, ast, lines, added, comments, content));
  errors.push(...missingEmptyStateErrors(filePath, ast, lines, added, comments, content));
  errors.push(...shortWaiverErrors(filePath, lines, added, comments));
}

if (errors.length > 0) {
  console.error(`${label}: user-experience check failed:`);
  for (const err of errors) {
    console.error(` - ${err}`);
  }
  process.exit(1);
}

console.log(`${label}: user-experience checks passed.`);
process.exit(0);
