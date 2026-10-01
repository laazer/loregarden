#!/usr/bin/env node
/**
 * Keep every surface on the theme.
 *
 * The app's colours live in one place: the custom properties on `:root` in
 * client/src/index.css. A surface that draws from them follows the theme; a
 * surface that names its own colour does not, and nobody notices until the
 * theme changes underneath it. Two shapes of that have already shipped:
 *
 * - a raw <button>/<input>/<select>/<textarea> with no class, which the browser
 *   paints as its own default — a white box on a near-black page;
 * - a card written as `background: white` plus a `prefers-color-scheme: dark`
 *   branch, which on a light-mode OS draws a light panel inside a dark app.
 *
 * Light mode is coming as a second token set (`[data-theme="light"]`), and every
 * hardcoded colour is a place it will not reach. So this gate holds the seam:
 *
 * Checks (diff-scoped, on .ts/.tsx/.css under the detected TypeScript root):
 *   1. A raw <button>, <input>, <select> or <textarea>, where the repo has
 *      themed primitives in components/ui to use instead.
 *   2. A colour literal — hex, rgb()/hsl()/oklch()/…, or a CSS colour name — in
 *      a stylesheet, outside a theme block (`:root`, `[data-theme…]`), which is
 *      where colours are defined rather than used.
 *   3. A colour literal in a JSX `style` object or an SVG colour attribute.
 *   4. A `prefers-color-scheme` branch, in CSS or in a matchMedia query. The
 *      theme is chosen by tokens, not by the operating system's preference.
 *
 * A case that is genuinely right says so on the line, with a reason:
 *
 *     <input type="file" hidden /> {/* theme-ok: never painted; the Upload button opens it *\/}
 *     background: #000; /* theme-ok: video letterbox, black in every theme *\/
 */

const fs = require("fs");
const path = require("path");
const { createRequire } = require("module");

const clientRoot = path.resolve(__dirname, "../../client");
const requireFromClient = createRequire(path.join(clientRoot, "package.json"));
const { parse } = requireFromClient("@typescript-eslint/typescript-estree");
const postcss = requireFromClient("postcss");

const {
  untrackedPaths,
  stagedAdditions,
  isTestFile,
  parseArgv,
  filesToCheck,
  tsSourceRoot,
} = require("./ts_git_diff.cjs");
const { waiversFor, spanTouched, shortWaiverErrors } = require("./gate_waivers.cjs");

const ALLOW_MARKER = "theme-ok:";
const SOURCE = /\.(ts|tsx|css)$/;

// Raw element → the themed primitive that replaces it.
const PRIMITIVES = new Map([
  ["button", "Button"],
  ["input", "Input"],
  ["select", "Select"],
  ["textarea", "Textarea"],
]);

// Where the primitives live, relative to the TypeScript root. Their own files
// necessarily render the raw element; nothing else should.
const PRIMITIVE_HOME = path.join("components", "ui");
const PRIMITIVE_MARKER = "Button.tsx";

// Selectors that define the theme. Colours belong here and nowhere else.
const THEME_SELECTOR = /:root\b|\[data-theme\b/;

const HEX = /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])/;
// `color(` is the CSS Color 4 function; `color-mix(` over tokens is the
// sanctioned way to derive a translucent tint and does not match (the `-`).
const COLOR_FUNCTION = /(?<![\w-])(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(/i;

// CSS Color 4 named colours. `transparent` and `currentColor` are deliberately
// absent: neither names a colour of its own, so both follow the theme.
const NAMED_COLORS = new Set(
  (
    "aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue " +
    "blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk " +
    "crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki " +
    "darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen " +
    "darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue " +
    "dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite " +
    "gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki " +
    "lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan " +
    "lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen " +
    "lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen " +
    "magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen " +
    "mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream " +
    "mistyrose moccasin navajowhite navy oldlace olive olivedrab orange orangered orchid " +
    "palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum " +
    "powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown " +
    "seagreen seashell sienna silver skyblue slateblue slategray slategrey snow springgreen " +
    "steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen"
  ).split(" "),
);

// A colour name is only a colour where a colour is expected: `animation: tan`
// and `grid-area: navy` are names. Hex and colour functions are unambiguous
// anywhere and are checked on every property.
const CSS_COLOR_PROPERTY =
  /^--|color|background|border|outline|shadow|^fill$|^stroke$|text-decoration|column-rule/;
const JS_COLOR_KEY =
  /^--|color|Color|background|Background|border|Border|outline|Outline|shadow|Shadow|^fill$|^stroke$/;
const SVG_COLOR_ATTRS = new Set([
  "color",
  "fill",
  "floodColor",
  "lightingColor",
  "stopColor",
  "stroke",
]);

const PREFERS_SCHEME = /prefers-color-scheme/;

const errors = [];

/**
 * The colour a value names, or null.
 *
 * Strings and url() are stripped first: `content: "#1"` is text, and
 * `fill: url(#gradient)` points at an element.
 */
function colourIn(value, namesCount) {
  const bare = value
    .replace(/url\([^)]*\)/gi, "")
    .replace(/"[^"]*"|'[^']*'/g, "");
  const hex = HEX.exec(bare);
  if (hex) return hex[0];
  const fn = COLOR_FUNCTION.exec(bare);
  if (fn) return `${fn[0]}…)`;
  if (namesCount) {
    for (const word of bare.toLowerCase().split(/[^a-z-]+/)) {
      if (NAMED_COLORS.has(word)) return word;
    }
  }
  return null;
}

function colourMessage(filePath, line, where, colour) {
  return (
    `${filePath}:${line}: ${where} hardcodes '${colour}' — it will not follow the theme ` +
    `when light mode lands; use a token from :root (var(--bg2), var(--tx), …), derive a ` +
    `tint with color-mix(in srgb, var(--ac) 30%, transparent), or add a token if none fits`
  );
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

function cssErrors(filePath, content, lines, added, waivers) {
  let root;
  try {
    root = postcss.parse(content, { from: filePath });
  } catch (err) {
    // Not a silent skip: a stylesheet this gate cannot read is one it did not check.
    return [
      `${filePath}: could not parse this stylesheet, so its colours were not checked: ` +
        `${err.reason ?? err.message}`,
    ];
  }

  const found = [];
  const flag = (node, message) => {
    const start = node.source.start.line;
    const end = node.source.end ? node.source.end.line : start;
    if (!spanTouched(added, start, end)) return;
    if (waivers.waived(start, end)) return;
    found.push(message(start));
  };

  root.walkAtRules((rule) => {
    if (!PREFERS_SCHEME.test(rule.params)) return;
    // Only the at-rule's own line: the body is reported (or not) on its merits.
    const line = rule.source.start.line;
    if (!spanTouched(added, line, line) || waivers.waived(line, line)) return;
    found.push(
      `${filePath}:${line}: @media (${rule.params.replace(/[()]/g, "").trim()}) themes by the ` +
        `OS preference — on a light-mode OS this draws a light surface inside the dark app; ` +
        `use the :root tokens, which [data-theme] will switch`,
    );
  });

  root.walkDecls((decl) => {
    if (inThemeBlock(decl)) return;
    const colour = colourIn(decl.value, CSS_COLOR_PROPERTY.test(decl.prop));
    if (colour === null) return;
    flag(decl, (line) => colourMessage(filePath, line, `'${decl.prop}'`, colour));
  });

  return found;
}

// --------------------------------------------------------------------------- //
// TS / TSX
// --------------------------------------------------------------------------- //

function parseFile(content) {
  try {
    return parse(content, { jsx: true, loc: true, range: false, comment: false });
  } catch {
    // silent-ok: tsc and oxlint both report syntax errors, and far better
    return null;
  }
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

function attributeName(attr) {
  return attr.type === "JSXAttribute" && attr.name && attr.name.type === "JSXIdentifier"
    ? attr.name.name
    : "";
}

/** The literal text of a string or template literal; null for anything computed. */
function literalText(node) {
  if (!node) return null;
  if (node.type === "Literal" && typeof node.value === "string") return node.value;
  if (node.type === "TemplateLiteral") return node.quasis.map((q) => q.value.cooked).join(" ");
  if (node.type === "JSXExpressionContainer") return literalText(node.expression);
  return null;
}

function propertyKey(prop) {
  if (prop.key.type === "Identifier") return prop.key.name;
  if (prop.key.type === "Literal") return String(prop.key.value);
  return "";
}

/** 1. A raw element where a themed primitive exists. */
function primitiveErrors(filePath, ast, added, waivers) {
  const found = [];
  walk(ast, (node) => {
    if (node.type !== "JSXOpeningElement" || node.name.type !== "JSXIdentifier") return;
    const replacement = PRIMITIVES.get(node.name.name);
    if (!replacement) return;
    // A hidden input is never painted; there is no surface to theme.
    const type = node.attributes.find((a) => attributeName(a) === "type");
    if (type && literalText(type.value) === "hidden") return;

    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return;
    if (waivers.waived(start, end)) return;
    found.push(
      `${filePath}:${start}: raw <${node.name.name}> — use <${replacement}> from ` +
        `components/ui/${replacement}, which draws from the theme tokens; the browser's ` +
        `default for an unstyled one is a light control in a dark app`,
    );
  });
  return found;
}

/** 3 and 4, in code: colour literals in style objects and SVG attributes, and OS-preference queries. */
function codeColourErrors(filePath, ast, added, waivers) {
  const found = [];
  const flag = (node, message) => {
    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return;
    if (waivers.waived(start, end)) return;
    found.push(message(start));
  };

  walk(ast, (node) => {
    if (node.type === "Literal" && typeof node.value === "string" && PREFERS_SCHEME.test(node.value)) {
      flag(node, (line) =>
        `${filePath}:${line}: queries prefers-color-scheme — the theme is chosen by tokens ` +
          `on :root, not by the OS; read the app's theme instead`,
      );
      return;
    }
    if (node.type !== "JSXAttribute") return;
    const name = attributeName(node);

    if (SVG_COLOR_ATTRS.has(name)) {
      const text = literalText(node.value);
      const colour = text === null ? null : colourIn(text, true);
      if (colour !== null) {
        flag(node, (line) => colourMessage(filePath, line, `${name}=`, colour));
      }
      return;
    }

    if (name !== "style" || !node.value || node.value.type !== "JSXExpressionContainer") return;
    walk(node.value.expression, (inner) => {
      if (inner.type !== "Property") return;
      const text = literalText(inner.value);
      if (text === null) return;
      const key = propertyKey(inner);
      const colour = colourIn(text, JS_COLOR_KEY.test(key));
      if (colour !== null) {
        flag(inner, (line) => colourMessage(filePath, line, `style.${key}`, colour));
      }
    });
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

const primitiveHome = path.join(tsSourceRoot(repoRoot), PRIMITIVE_HOME);
// Asking for <Button> in a repo that has none would be a finding with no fix.
const hasPrimitives = fs.existsSync(path.join(primitiveHome, PRIMITIVE_MARKER));

const untracked = new Set(
  diffScope === "worktree" ? untrackedPaths(repoRoot).map((p) => path.resolve(repoRoot, p)) : [],
);

for (const filePath of args) {
  if (!fs.existsSync(filePath) || isTestFile(filePath)) continue;
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
    errors.push(...cssErrors(filePath, content, lines, added, waivers));
  } else {
    const ast = parseFile(content);
    if (!ast) continue;
    const isPrimitive = absolute.startsWith(`${primitiveHome}${path.sep}`);
    if (hasPrimitives && !isPrimitive) {
      errors.push(...primitiveErrors(filePath, ast, added, waivers));
    }
    errors.push(...codeColourErrors(filePath, ast, added, waivers));
  }
  errors.push(
    ...shortWaiverErrors(
      ALLOW_MARKER,
      filePath,
      lines,
      added,
      waivers,
      "say why this surface is right in every theme",
    ),
  );
}

if (errors.length > 0) {
  console.error(`${label}: theme check failed:`);
  for (const err of errors) {
    console.error(` - ${err}`);
  }
  process.exit(1);
}

console.log(`${label}: theme checks passed.`);
process.exit(0);
