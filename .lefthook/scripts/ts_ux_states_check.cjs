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
 * 5–8 make a surface drivable by an agent granted permission to operate it,
 * which reads the accessibility tree, acts by role and name, and reads state
 * back to confirm the action landed:
 *   5. A field (input, select, textarea, contentEditable, field role) with no
 *      label, and a contentEditable with no role.
 *   6. A widget role without its state attribute, aria-haspopup without
 *      aria-expanded, or a click handler flipping a boolean that exposes none.
 *   7. Hover with no focus equivalent; a drag source with no key handler; a drop
 *      zone with no other way in.
 *   8. A <canvas> that takes input — its targets exist only as pixels.
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

// --------------------------------------------------------------------------- //
// 5–8: drivable by an agent.
//
// An agent operating the app — a browser tool, an MCP-driven test, anything
// granted permission to act — reads the accessibility tree, finds a control by
// role and name, reads its state, and clicks or types. It does not see pixels it
// can trust, it cannot reliably hover or drag, and a canvas is one opaque node.
// Checks 1 and 2 already demand a name and keyboard reach; these demand the rest
// of what that reader needs. A person on a screen reader needs exactly the same.
// --------------------------------------------------------------------------- //

// Inputs the browser names from their own attributes (`value`, `alt`), or that
// are not in the tree at all.
const SELF_NAMED_INPUT_TYPES = new Set(["hidden", "submit", "button", "reset", "image"]);

// Roles that make an element a field someone types into or sets a value on.
const FIELD_ROLES = new Set(["textbox", "searchbox", "combobox", "slider", "spinbutton"]);

// A widget role is a promise that the widget reports where it is. Without the
// attribute a reader sees "tab" and cannot tell which one is open, or "switch"
// and cannot tell whether it is on — so it cannot tell whether a click worked.
const ROLE_STATE_ATTRS = new Map([
  ["checkbox", ["aria-checked"]],
  ["switch", ["aria-checked"]],
  ["radio", ["aria-checked"]],
  ["menuitemcheckbox", ["aria-checked"]],
  ["menuitemradio", ["aria-checked"]],
  ["tab", ["aria-selected"]],
  ["option", ["aria-selected"]],
  ["combobox", ["aria-expanded"]],
  ["slider", ["aria-valuenow"]],
  ["spinbutton", ["aria-valuenow"]],
]);

// Any of these lets a toggle say which way it is set.
const TOGGLE_STATE_ATTRS = ["aria-pressed", "aria-expanded", "aria-checked", "aria-selected"];

// Rows of a composite widget: hovering one moves the highlight, and the widget
// already moves it from the keyboard (arrows, aria-activedescendant). The hover
// is a pointer convenience, not the only way to the row.
const HOVER_EXEMPT_ROLES = new Set([
  "option",
  "menuitem",
  "menuitemcheckbox",
  "menuitemradio",
  "row",
  "gridcell",
  "treeitem",
]);

const HOVER_ATTRS = ["onMouseEnter", "onMouseOver"];
const FOCUS_ATTRS = ["onFocus", "onFocusCapture"];
const KEY_ATTRS = ["onKeyDown", "onKeyUp", "onKeyPress"];

// Handlers that make a canvas something you operate rather than something you look at.
const CANVAS_INPUT_ATTR = /^on(?:Click|DoubleClick|Pointer\w+|Mouse\w+|Touch\w+|Wheel|Key\w+|Drag\w*|Drop)$/;
const CANVAS_LISTENER = /\.addEventListener\(\s*["'`](?:click|dblclick|pointer\w+|mouse\w+|touch\w+|wheel|key\w+|drag\w*|drop)["'`]/;

/** Like `walk`, but hands each node the JSX elements that enclose it. */
function walkElements(node, visit, ancestors = []) {
  if (!node || typeof node.type !== "string") return;
  const isElement = node.type === "JSXElement";
  if (isElement) visit(node, ancestors);
  const next = isElement ? [...ancestors, node] : ancestors;
  for (const key of Object.keys(node)) {
    if (key === "parent" || key === "loc") continue;
    const value = node[key];
    if (Array.isArray(value)) {
      for (const child of value) walkElements(child, visit, next);
    } else if (value && typeof value.type === "string") {
      walkElements(value, visit, next);
    }
  }
}

/** The attribute node itself, or null — for when presence and value both matter. */
function attributeNode(opening, wanted) {
  for (const attr of opening.attributes || []) {
    if (attr.type === "JSXSpreadAttribute") continue;
    if (attr.name && attr.name.name === wanted) return attr;
  }
  return null;
}

/** `false` only for an attribute written as literally false; present-and-unknown is true. */
function attributeIsOn(opening, wanted) {
  const attr = attributeNode(opening, wanted);
  if (!attr) return false;
  const value = attr.value;
  if (!value) return true;
  if (value.type === "Literal") return value.value !== "false" && value.value !== false;
  if (value.type === "JSXExpressionContainer" && value.expression.type === "Literal") {
    return value.expression.value !== false && value.expression.value !== "false";
  }
  return true;
}

/** Every literal `htmlFor`, and whether any is computed — the ids a <label> points at. */
function labelTargets(ast) {
  const literal = new Set();
  let computed = false;
  walk(ast, (node) => {
    if (node.type !== "JSXOpeningElement") return;
    const attr = attributeNode(node, "htmlFor");
    if (!attr) return;
    if (attr.value && attr.value.type === "Literal") literal.add(attr.value.value);
    else computed = true;
  });
  return { literal, computed };
}

/** Whether a <label htmlFor> in this file can be pointing at this element's id. */
function labelledById(opening, targets) {
  const attr = attributeNode(opening, "id");
  if (!attr) return false;
  if (targets.computed) return true;
  if (attr.value && attr.value.type === "Literal") return targets.literal.has(attr.value.value);
  // A computed id with only literal htmlFors may still match; the gate cannot tell.
  return targets.literal.size > 0;
}

/** What kind of field this element is, or "" when it is not one. */
function fieldKind(node) {
  const opening = node.openingElement;
  const name = elementName(opening);
  if (name === "input") {
    return SELF_NAMED_INPUT_TYPES.has(attributeValue(opening, "type")) ? "" : "input";
  }
  if (name === "select" || name === "textarea") return name;
  if (isIntrinsic(name) && attributeIsOn(opening, "contentEditable")) return "contentEditable";
  if (FIELD_ROLES.has(attributeValue(opening, "role"))) return `role="${attributeValue(opening, "role")}"`;
  return "";
}

/**
 * 5. A field nothing names.
 *
 * `form_input` and `find` locate a field by its name. An input with only a
 * placeholder has a hint that disappears on the first keystroke, and one with
 * nothing has no handle at all — the agent is left guessing by position, and so
 * is everyone else who cannot see the layout.
 */
function unlabelledFieldErrors(filePath, ast, lines, added, comments) {
  const targets = labelTargets(ast);
  const found = [];
  walkElements(ast, (node, ancestors) => {
    const kind = fieldKind(node);
    if (!kind) return;

    const start = node.loc.start.line;
    const end = node.openingElement.loc.end.line;
    if (!spanTouched(added, start, end)) return;
    if (spanWaived(lines, start, end, comments)) return;

    const opening = node.openingElement;
    const { names, hasSpread } = attributeNames(opening);
    if (hasSpread) return;

    const named =
      [...NAMING_ATTRS].some((attr) => names.has(attr)) ||
      ancestors.some((el) => elementName(el.openingElement) === "label") ||
      labelledById(opening, targets);

    if (!named) {
      found.push(
        `${filePath}:${start}: <${elementName(opening)}> (${kind}) has no label — no ` +
          `aria-label, no aria-labelledby, no wrapping <label>, no <label htmlFor> pointing ` +
          `at its id; an agent or screen reader cannot find it by name; label it`,
      );
    }
    if (kind === "contentEditable" && !names.has("role")) {
      found.push(
        `${filePath}:${start}: <${elementName(opening)} contentEditable> has no role — it ` +
          `reaches the accessibility tree as a generic element, not a field; add ` +
          `role="textbox" (and aria-multiline when it takes more than one line)`,
      );
    }
  });
  return found;
}

/** Local `const name = () => …` bodies, so `onClick={toggle}` can be read through. */
function localFunctions(ast) {
  const bodies = new Map();
  walk(ast, (node) => {
    if (node.type === "VariableDeclarator" && node.id && node.id.type === "Identifier") {
      const init = node.init;
      if (init && (init.type === "ArrowFunctionExpression" || init.type === "FunctionExpression")) {
        bodies.set(node.id.name, init);
      }
    }
    if (node.type === "FunctionDeclaration" && node.id) bodies.set(node.id.name, node);
  });
  return bodies;
}

/** `!x`, or `(x) => !x` — the argument a setter gets when it is flipping a boolean. */
function isNegation(arg) {
  if (!arg) return false;
  if (arg.type === "UnaryExpression" && arg.operator === "!") return true;
  if (arg.type === "ArrowFunctionExpression") {
    const body = arg.body;
    if (body.type === "UnaryExpression" && body.operator === "!") return true;
    if (body.type === "BlockStatement") {
      const last = body.body[body.body.length - 1];
      return Boolean(last && last.type === "ReturnStatement" && isNegation(last.argument));
    }
  }
  return false;
}

/** Whether a handler flips a piece of boolean state: `setOpen(!open)`, `setOpen((o) => !o)`. */
function handlerToggles(handler, functions) {
  let fn = handler;
  if (fn && fn.type === "Identifier") fn = functions.get(fn.name);
  if (!fn) return false;
  let toggles = false;
  walk(fn, (node) => {
    if (node.type !== "CallExpression" || node.callee.type !== "Identifier") return;
    if (/^set[A-Z]/.test(node.callee.name) && isNegation(node.arguments[0])) toggles = true;
  });
  return toggles;
}

/**
 * 6. A widget that does not say what state it is in.
 *
 * An agent that clicks a tab, a switch, or a disclosure button verifies the
 * click by reading the state back. With no aria-selected / aria-checked /
 * aria-expanded / aria-pressed there is nothing to read: the click may have
 * worked, and nothing in the tree says so.
 */
function widgetStateErrors(filePath, ast, lines, added, comments) {
  const functions = localFunctions(ast);
  const found = [];
  walk(ast, (node) => {
    if (node.type !== "JSXOpeningElement") return;
    const { names, hasSpread } = attributeNames(node);
    if (hasSpread) return;

    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return;
    if (spanWaived(lines, start, end, comments)) return;

    const name = elementName(node);
    // A native checkbox or radio reports `checked` itself, whatever its role says.
    const nativeState =
      name === "input" && ["checkbox", "radio"].includes(attributeValue(node, "type"));
    if (nativeState) return;

    const role = attributeValue(node, "role");
    const required = ROLE_STATE_ATTRS.get(role);
    if (required && !required.some((attr) => names.has(attr))) {
      found.push(
        `${filePath}:${start}: role="${role}" without ${required.join("/")} — a reader sees ` +
          `the ${role} but not whether it is selected, checked or open, so it cannot confirm a ` +
          `click landed; set ${required.join("/")} from the same state the styling reads`,
      );
      return;
    }

    if (names.has("aria-haspopup") && !names.has("aria-expanded")) {
      found.push(
        `${filePath}:${start}: aria-haspopup without aria-expanded — a reader is told this ` +
          `opens something, never whether it is open; add aria-expanded`,
      );
      return;
    }

    const onClick = attributeNode(node, "onClick");
    if (!onClick || !onClick.value || onClick.value.type !== "JSXExpressionContainer") return;
    if (TOGGLE_STATE_ATTRS.some((attr) => names.has(attr))) return;
    if (!handlerToggles(onClick.value.expression, functions)) return;

    found.push(
      `${filePath}:${start}: <${name}> flips state on click but exposes none of ` +
        `${TOGGLE_STATE_ATTRS.join(", ")} — the toggle looks identical on and off to an ` +
        `agent or screen reader; add aria-expanded for show/hide, aria-pressed for on/off`,
    );
  });
  return found;
}

/**
 * 7. An action behind a hover, or behind a drag.
 *
 * Pointer automation can hover, but it has to know to, and a keyboard cannot at
 * all; whatever onMouseEnter reveals must also be revealed on focus. A drag is
 * worse — synthesised drag events are unreliable across every driver, and HTML5
 * drag-and-drop has no keyboard path — so a drag source needs a key handler that
 * does the same move, and a drop zone for outside data needs a control that takes
 * the same input.
 */
function pointerOnlyErrors(filePath, ast, lines, added, comments, content) {
  const hasDragSource = /\bonDragStart\b/.test(content);
  const hasFileInput = /type\s*=\s*["']file["']/.test(content);
  const found = [];
  walk(ast, (node) => {
    if (node.type !== "JSXOpeningElement") return;
    const { names, hasSpread } = attributeNames(node);
    if (hasSpread) return;

    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return;
    if (spanWaived(lines, start, end, comments)) return;

    const name = elementName(node);
    const role = attributeValue(node, "role");

    const hover = HOVER_ATTRS.find((attr) => names.has(attr));
    if (hover && !HOVER_EXEMPT_ROLES.has(role) && !FOCUS_ATTRS.some((attr) => names.has(attr))) {
      found.push(
        `${filePath}:${start}: <${name} ${hover}> with no onFocus — whatever the hover ` +
          `reveals, a keyboard cannot reach and an agent has to guess to hover for; do the ` +
          `same on focus (and undo it on blur)`,
      );
    }

    const dragSource = names.has("onDragStart") || (names.has("draggable") && attributeIsOn(node, "draggable"));
    if (dragSource && !KEY_ATTRS.some((attr) => names.has(attr))) {
      found.push(
        `${filePath}:${start}: <${name}> can be dragged but has no key handler — drag is ` +
          `the only way to move it, and neither a keyboard nor an agent can drag reliably; ` +
          `add onKeyDown doing the same move, or a control that does, and waive naming it`,
      );
    }

    if (names.has("onDrop") && !hasDragSource && !hasFileInput) {
      found.push(
        `${filePath}:${start}: <${name} onDrop> takes dropped data, and nothing in this file ` +
          `takes the same input another way — add an <input type="file"> beside it, or waive ` +
          `with 'ux-ok:' naming the component that does (paste is no alternative: an agent ` +
          `cannot paste a file)`,
      );
    }
  });
  return found;
}

/**
 * 8. A canvas you operate.
 *
 * A canvas is one node in the accessibility tree; everything drawn inside it is
 * pixels. Once it takes clicks, drags or keys, its targets exist for a mouse and
 * for nothing else — an agent can only click coordinates it read off a
 * screenshot, and verify nothing. The actions need DOM controls: buttons laid
 * over it, or a list beside it, that do the same thing.
 */
function interactiveCanvasErrors(filePath, ast, lines, added, comments, content) {
  const listens = CANVAS_LISTENER.test(content);
  const found = [];
  walk(ast, (node) => {
    if (node.type !== "JSXOpeningElement" || elementName(node) !== "canvas") return;
    const { names } = attributeNames(node);
    const handler = [...names].find((attr) => CANVAS_INPUT_ATTR.test(attr));
    if (!handler && !(listens && names.has("ref"))) return;

    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return;
    if (spanWaived(lines, start, end, comments)) return;

    found.push(
      `${filePath}:${start}: <canvas> takes input (${handler || "addEventListener on its ref"}) ` +
        `— what it draws is invisible to the accessibility tree, so an agent can neither find ` +
        `nor verify its targets; expose each action as a DOM control, then waive with ` +
        `'ux-ok:' naming where those controls are`,
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
  errors.push(...unlabelledFieldErrors(filePath, ast, lines, added, comments));
  errors.push(...widgetStateErrors(filePath, ast, lines, added, comments));
  errors.push(...pointerOnlyErrors(filePath, ast, lines, added, comments, content));
  errors.push(...interactiveCanvasErrors(filePath, ast, lines, added, comments, content));
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
