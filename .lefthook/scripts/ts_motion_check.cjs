#!/usr/bin/env node
/**
 * Keep motion on the tokens, off the layout, and behind the reader's preference.
 *
 * Motion has the same failure as colour did: every surface picked its own. The
 * client carried about 31 distinct hardcoded durations, a sidebar that animates
 * `width` (a relayout every frame), and 17 animated stylesheets that ignored
 * `prefers-reduced-motion`. The rules live beside the tokens on `:root` in
 * client/src/index.css; this gate holds them on new lines.
 *
 * Checks (diff-scoped, on .css under the detected TypeScript root):
 *   1. A transition or keyframe that animates a layout property (width, height,
 *      top, margin, …), or `transition: all`, which includes them. Animate
 *      transform and opacity instead.
 *   2. A hardcoded UI duration (up to 400ms) in a transition or animation, where
 *      the repo defines the `--t-*` tokens. Longer durations are ambient loops
 *      (pulses, shimmers) that no token describes, and are left alone.
 *   3. An animation or movement transition in a repo with no reduced-motion
 *      policy: neither a global `@media (prefers-reduced-motion: reduce)` rule
 *      over `*` in the root stylesheet, nor one in the file itself.
 *
 * A case that is genuinely right says so on the line, with a reason:
 *
 *     transition: max-height var(--t-med); /* motion-ok: height is unknown; the body clips *\/
 */

const fs = require("fs");
const path = require("path");

const { requireClientModule } = require("./gate_client_modules.cjs");
const {
  untrackedPaths,
  stagedAdditions,
  isTestFile,
  parseArgv,
  filesToCheck,
  tsSourceRoot,
} = require("./ts_git_diff.cjs");
const { waiversFor, spanTouched, shortWaiverErrors } = require("./gate_waivers.cjs");

const ALLOW_MARKER = "motion-ok:";
const SOURCE = /\.css$/;
const ROOT_STYLESHEET = "index.css";

const LAYOUT_PROPERTY =
  /^(?:width|height|(?:min|max)-(?:width|height)|top|right|bottom|left|inset(?:-.*)?|margin(?:-.*)?|padding(?:-.*)?|border(?:-(?:top|right|bottom|left))?-width|font-size|line-height|flex-basis|gap|row-gap|column-gap|grid-template-(?:rows|columns))$/;
// Transitions on these move nothing on screen; a reader who asked for less
// motion is not served by suppressing a colour fade.
const STILL_PROPERTY =
  /^(?:opacity|visibility|color|background(?:-color)?|border(?:-(?:top|right|bottom|left))?-color|outline-color|fill|stroke|box-shadow|text-decoration-color|filter)$/;
const TIME = /^(\d*\.?\d+)(ms|s)$/i;
// A token or computed time fills the same slot in a shorthand as a literal does.
const TIME_EXPRESSION = /^(?:var\(--t-|calc\()/i;
const UI_DURATION_CEILING_MS = 400;

const errors = [];

function postcss() {
  return requireClientModule("postcss");
}

function parseCss(filePath, content) {
  try {
    return { root: postcss().parse(content, { from: filePath }) };
  } catch (err) {
    // Not a silent skip: a stylesheet this gate cannot read is one it did not check.
    return {
      error: `${filePath}: could not parse this stylesheet, so its motion was not checked: ${err.reason ?? err.message}`,
    };
  }
}

/** Split on a top-level separator, leaving var(--a, b) and cubic-bezier(…) whole. */
function splitTopLevel(value, separator) {
  const out = [];
  let depth = 0;
  let current = "";
  for (const ch of value) {
    if (ch === "(") depth++;
    if (ch === ")") depth--;
    if (separator.test(ch) && depth === 0) {
      if (current.trim()) out.push(current.trim());
      current = "";
    } else {
      current += ch;
    }
  }
  if (current.trim()) out.push(current.trim());
  return out;
}

const layers = (value) => splitTopLevel(value, /,/);
const words = (layer) => splitTopLevel(layer, /\s/);
const isTimeSlot = (word) => TIME.test(word) || TIME_EXPRESSION.test(word);

/** The property each layer of a `transition` shorthand names (`all` when none is given). */
function transitionedProperties(decl) {
  if (decl.prop === "transition-property") return layers(decl.value).map((p) => p.toLowerCase());
  return layers(decl.value).map((layer) => {
    const named = words(layer).find(
      (w) => !isTimeSlot(w) && !w.includes("(") && !/^(?:ease|linear|step)/i.test(w) && w !== "none",
    );
    return (named || "all").toLowerCase();
  });
}

function isNone(value) {
  return /^\s*none\s*$/i.test(value);
}

function toMs(amount, unit) {
  return unit.toLowerCase() === "s" ? Number(amount) * 1000 : Number(amount);
}

function nearestToken(ms) {
  if (ms <= 160) return "var(--t-fast)";
  if (ms <= 250) return "var(--t-med)";
  return "var(--t-slow)";
}

/** Durations in a transition/animation value: the first time in each layer (the second is a delay). */
function durationsIn(decl) {
  if (/-delay$/.test(decl.prop)) return [];
  const everySlot = /-duration$/.test(decl.prop);
  const found = [];
  for (const layer of layers(decl.value)) {
    const slots = words(layer).filter(isTimeSlot);
    for (const slot of everySlot ? slots : slots.slice(0, 1)) {
      const match = TIME.exec(slot);
      if (match) found.push({ text: slot, ms: toMs(match[1], match[2]) });
    }
  }
  return found;
}

function inKeyframes(node) {
  for (let parent = node.parent; parent; parent = parent.parent) {
    if (parent.type === "atrule" && /keyframes$/i.test(parent.name)) return true;
  }
  return false;
}

function inReducedMotionBlock(node) {
  for (let parent = node.parent; parent; parent = parent.parent) {
    if (parent.type === "atrule" && /prefers-reduced-motion/.test(parent.params)) return true;
  }
  return false;
}

function hasReducedMotionRule(root, { global }) {
  let found = false;
  root.walkAtRules((rule) => {
    if (!/prefers-reduced-motion\s*:\s*reduce/.test(rule.params)) return;
    if (!global) {
      found = true;
      return;
    }
    rule.walkRules((inner) => {
      if (inner.selectors.some((s) => s.trim() === "*")) found = true;
    });
  });
  return found;
}

function rootStylesheetFacts(repoRoot) {
  const file = path.join(tsSourceRoot(repoRoot), ROOT_STYLESHEET);
  if (!fs.existsSync(file)) return { tokens: false, globalPolicy: false };
  const parsed = parseCss(file, fs.readFileSync(file, "utf8"));
  if (parsed.error) {
    errors.push(parsed.error);
    return { tokens: false, globalPolicy: false };
  }
  let tokens = false;
  parsed.root.walkDecls("--t-fast", () => {
    tokens = true;
  });
  return { tokens, globalPolicy: hasReducedMotionRule(parsed.root, { global: true }) };
}

function moves(decl) {
  if (/^animation(?:-name)?$/.test(decl.prop)) return !isNone(decl.value);
  if (decl.prop === "transition" || decl.prop === "transition-property") {
    if (isNone(decl.value)) return false;
    return transitionedProperties(decl).some((p) => !STILL_PROPERTY.test(p));
  }
  return false;
}

function cssErrors(filePath, content, added, waivers, facts) {
  const parsed = parseCss(filePath, content);
  if (parsed.error) return [parsed.error];
  const { root } = parsed;
  const policy = facts.globalPolicy || hasReducedMotionRule(root, { global: false });

  const found = [];
  const flag = (decl, message) => {
    const start = decl.source.start.line;
    const end = decl.source.end ? decl.source.end.line : start;
    if (!spanTouched(added, start, end) || waivers.waived(start, end)) return;
    found.push(`${filePath}:${start}: ${message}`);
  };

  root.walkDecls((decl) => {
    if (inReducedMotionBlock(decl)) return;

    // 1. Layout properties, in a keyframe or a transition.
    if (inKeyframes(decl) && LAYOUT_PROPERTY.test(decl.prop)) {
      flag(decl, `a keyframe animates '${decl.prop}', which relayouts the page every frame; animate transform (translate/scale) or opacity instead`);
      return;
    }
    if (decl.prop === "transition" || decl.prop === "transition-property") {
      for (const prop of transitionedProperties(decl)) {
        if (prop === "all") {
          flag(decl, `'transition: all' animates whatever changes, layout included; name the properties (transform, opacity, …)`);
        } else if (LAYOUT_PROPERTY.test(prop)) {
          flag(decl, `transitions '${prop}', which relayouts the page every frame; animate transform (translate/scale) or opacity instead`);
        }
      }
    }

    // 2. Hardcoded UI durations, where tokens exist.
    if (facts.tokens && /^(?:transition|animation)(?:-duration)?$/.test(decl.prop)) {
      for (const { text, ms } of durationsIn(decl)) {
        if (ms > 0 && ms <= UI_DURATION_CEILING_MS) {
          flag(decl, `hardcodes '${text}' — use ${nearestToken(ms)} (fast: hover/press, med: toggle/dropdown/toast, slow: modal/panel; an exit runs one step faster)`);
        }
      }
    }

    // 3. Motion with no reduced-motion policy anywhere.
    if (!policy && moves(decl)) {
      flag(decl, `'${decl.prop}' moves, and neither ${ROOT_STYLESHEET} nor this file has a '@media (prefers-reduced-motion: reduce)' rule; add one so a reader who asked for less motion gets a fade or a held frame`);
    }
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

const facts = rootStylesheetFacts(repoRoot);
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

  errors.push(...cssErrors(filePath, content, added, waivers, facts));
  errors.push(
    ...shortWaiverErrors(
      ALLOW_MARKER,
      filePath,
      lines,
      added,
      waivers,
      "say why this motion is right",
    ),
  );
}

if (errors.length > 0) {
  console.error(`${label}: motion check failed:`);
  for (const err of errors) {
    console.error(` - ${err}`);
  }
  process.exit(1);
}

console.log(`${label}: motion checks passed.`);
process.exit(0);
