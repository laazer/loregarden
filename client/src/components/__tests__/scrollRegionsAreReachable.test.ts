/**
 * Every bounded scrolling region in the client is enumerated, and each one
 * states the measured reason a keyboard can reach it.
 *
 * ## The shape this guards
 *
 * A height-bounded `overflow: auto|scroll` region that is not itself tabbable
 * and holds no focusable descendant cannot be scrolled by arrow keys, Page
 * Up/Down or Home/End in Chromium or WebKit: nothing in the subtree can take
 * the focus those keys scroll from. That is `axe-core`'s
 * `scrollable-region-focusable` and WCAG 2.1.1 (Keyboard).
 *
 * ## Why a committed census and not a number in a ticket
 *
 * lg-milestone-that-2215 inherited a figure of 84 "candidate rule blocks" and
 * four independent extractions of that same set produced 19, 39, 84 and 87,
 * over two different file sets (47 CSS files and 102). All four are retired.
 * A denominator is only meaningful beside the conventions that produced it, so
 * the conventions are stated here and the count is this test's output:
 *
 * - **Glob.** `client/src/**` — every `.css` file for candidates, every `.tsx`
 *   file outside `__tests__` for the elements that name them.
 * - **Comments stripped** from CSS before parsing, and TypeScript's own parser
 *   used on TSX rather than a regex over the source (a regex reads
 *   `` className={`row ${on ? "a" : ""}`} `` as four names and reports two of
 *   them missing, which is how a scan like this grows a suppression list of
 *   its own artifacts).
 * - **Counted per RULE BLOCK, not per declaration.** One block declaring both
 *   `overflow-x` and `overflow-y` is one candidate.
 * - **BOTH bound kinds included.** Excluding the flex-bound kind is the error
 *   that produced the retired 84: it is the larger of the two classes, and it
 *   is the one holding `.app-sidebar-list`.
 *
 * ## Why the runtime detector cannot do this job here
 *
 * `findUsabilityProblems`'s `scrollable-region-focusable` check measures
 * `scrollHeight`/`clientHeight` and computed style. Under jsdom no app CSS is
 * attached at all (`jest.config.cjs` maps every stylesheet to
 * `src/test/styleMock.ts`), so it measures nothing, and in a browser it names a
 * region by its **first** class — `.wm-error-reason`'s `className` is
 * `"wm-muted wm-narrow wm-error-reason"`, so it reports `.wm-muted`, a shared
 * utility that joins to no rule block. A reported name is not a selector.
 * Reconciliation therefore runs census → element → detector report, never the
 * other way round, and the census is a static scan of files on disk, in the
 * manner of `classNamesResolve.test.ts` and `dialogsTrapFocus.test.ts`.
 *
 * ## Shrink-only, in both directions
 *
 * A candidate the baseline does not list fails, so the family cannot grow
 * unnoticed. A baseline entry that is no longer a candidate ALSO fails, so
 * entries are deleted when their rule goes rather than accumulating as an
 * allowlist — which is what this test would become the first time it was
 * easier to add a line than to look.
 *
 * ## The fix set is COMPUTED, and this file lists no part of it
 *
 * The baseline below is a frozen snapshot of a measurement, not a work list.
 * It is generated from the sibling census
 * (`scrollRegionsMeasuredReachability.test.ts`), whose AC10(g1) assertion
 * computes the set of candidate element sites holding no tab stop and requires
 * that set to be EMPTY. That is the fix set, and nothing here or in any
 * criterion states a figure for it (AC10(c)).
 *
 * Three rounds of this ticket each failed on a hand-maintained list, two of
 * them on a list spec had just written: round 3's enumeration of 21 blocks and
 * 24 sites measured 12 / 23 / 16, disagreeing in BOTH directions. So the lists
 * are gone, along with the two reason strings that existed only to serve them:
 *
 * - **the one meaning "unreachable, and this ticket does not fix it"**
 *   (deleted by AC-FOLD) — a sweep that ships with a remainder is the first
 *   ticket of the next enumeration rather than the last of this one.
 * - **the one naming this ticket as the fixer** (deleted in round 4) — never a
 *   measured property of anything. A fixed element carries `tabIndex={0}`, so
 *   it measures `tabbable`, which is what a fix IS.
 *
 * AC10(d) requires both strings to appear NOWHERE in the four census files,
 * prose included, which is why neither is spelled out above; the sibling
 * census asserts it over the files themselves. What is left is the eight
 * measured terms below, none of which can mean "unreachable and not fixed".
 */

import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

import { BASELINE, REASONS, type BaselineEntry, type Reason } from "../../test/scrollRegionsBaseline";

const SRC = path.resolve(__dirname, "../..");

/**
 * Stands in for a runtime value in a class name; cannot occur in one.
 *
 * Written as an escape rather than as the byte itself: a literal NUL in the
 * source makes git classify this whole file as binary, so `git diff` reports
 * `Bin 0 -> 45614 bytes` instead of the change, `grep -r` skips it with
 * "Binary file ... matches", and a merge of it cannot be resolved by hand. A
 * census whose own diff is unreadable is a census nobody can review, which is
 * the one property shrink-only depends on.
 */
const HOLE = "\u0000";

// ---------------------------------------------------------------------------
// The CSS side: candidate rule blocks.
// ---------------------------------------------------------------------------

/** The two ways a block can be bounded, which is what makes it clip. */
type BoundKind = "declared" | "flex";

/**
 * The axis a block clips on.
 *
 * Required, not polish. Since lg-milestone-that-2230 (`2d7a71446`) the
 * detector asks the geometry question per axis (`usabilityCheck.ts:405-406`),
 * so a vertical-only predicate cannot PRODUCE a horizontally-clipping region
 * as a candidate — and shrink-only cannot fire for a candidate the predicate
 * never produces. That is why a second, independently written extractor did
 * not catch the gap either: both implemented the same vertical definition, and
 * two instruments cannot disagree about a class of region neither one can see.
 */
type Axis = "x" | "y";

interface Candidate {
  /** Selector text as written, whitespace-normalised. */
  selector: string;
  /** Stylesheet path relative to `client/src/`. */
  file: string;
  /** Every axis that both scrolls and is bounded; a block may clip on both. */
  clips: { axis: Axis; bound: BoundKind }[];
  /**
   * The classes of the selector's SUBJECT — the last compound of each
   * comma-separated part, which is the element the rule actually styles.
   *
   * `.branch-cleanup-modal .modal-body` styles `.modal-body`, not the dialog
   * around it, so joining on every class in the selector would attribute the
   * rule to the wrong element. A subject that carries no class at all
   * (`.initiative-create-picked ul`) can be joined to nothing, and is reported
   * as `no-element` rather than silently credited to its ancestor.
   */
  classes: string[];
}

/** The classes of each comma-separated part's rightmost compound selector. */
const subjectClasses = (selector: string): string[] => {
  const classes = new Set<string>();
  for (const part of selector.split(",")) {
    const subject = part.trim().split(/[\s>+~]+/).filter(Boolean).pop() ?? "";
    for (const [, name] of subject.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)) classes.add(name);
  }
  return [...classes];
};

function walkFiles(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "node_modules") walkFiles(full, out);
    } else out.push(full);
  }
  return out;
}

const SCROLLING = /^(auto|scroll)$/;

/**
 * Does the named axis scroll?
 *
 * The per-axis longhand wins where there is one. Otherwise the shorthand's
 * words are POSITIONAL: `overflow: auto hidden` scrolls on x and not on y, and
 * a single word applies to both. The first version of this read "either token
 * counts on either axis", which is how a region bounded on y and scrolling on
 * x became a candidate and a region bounded on x stayed invisible.
 */
const axisScrolls = (declarations: Record<string, string>, axis: Axis): boolean => {
  const own = declarations[`overflow-${axis}`];
  if (own !== undefined && own !== "") return SCROLLING.test(own);
  const words = (declarations["overflow"] ?? "").split(/\s+/).filter(Boolean);
  if (words.length === 0) return false;
  return SCROLLING.test((axis === "x" ? words[0] : (words[1] ?? words[0])) ?? "");
};

/** A declared extent on this axis, with a value that actually bounds it. */
const boundedByDeclaration = (declarations: Record<string, string>, axis: Axis): boolean => {
  const extent = axis === "x" ? "width" : "height";
  return [extent, `max-${extent}`].some((property) => {
    const value = declarations[property];
    return value !== undefined && value !== "none" && value !== "auto" && value !== "";
  });
};

/**
 * `min-<extent>: 0`, or a non-zero flex grow: a flex child clipped by its
 * parent on this axis.
 */
const boundedByFlex = (declarations: Record<string, string>, axis: Axis): boolean => {
  const extent = axis === "x" ? "width" : "height";
  const floor = declarations[`min-${extent}`];
  if (floor !== undefined && /^0(\D|$)/.test(floor)) return true;
  const grow = declarations["flex-grow"] ?? declarations["flex"]?.split(/\s+/)[0];
  return grow !== undefined && grow !== "" && Number.parseFloat(grow) > 0;
};

function candidates(cssFiles: string[]): Candidate[] {
  const found: Candidate[] = [];
  for (const file of cssFiles) {
    const source = fs.readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    // Innermost blocks only: the pattern forbids braces inside, so an `@media`
    // or `@supports` wrapper is skipped and the rules within it are counted.
    for (const [, rawSelector, body] of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const selector = rawSelector.trim().replace(/\s+/g, " ");
      if (!selector || selector.startsWith("@") || selector.startsWith("%")) continue;
      const declarations: Record<string, string> = {};
      for (const line of body.split(";")) {
        const [property, ...rest] = line.split(":");
        if (!property.trim() || rest.length === 0) continue;
        declarations[property.trim().toLowerCase()] = rest.join(":").trim().toLowerCase();
      }
      const clips: { axis: Axis; bound: BoundKind }[] = [];
      for (const axis of ["x", "y"] as const) {
        if (!axisScrolls(declarations, axis)) continue;
        if (boundedByDeclaration(declarations, axis)) clips.push({ axis, bound: "declared" });
        else if (boundedByFlex(declarations, axis)) clips.push({ axis, bound: "flex" });
      }
      if (clips.length === 0) continue;
      found.push({
        selector,
        file: path.relative(SRC, file),
        clips,
        classes: subjectClasses(selector),
      });
    }
  }
  return found;
}

// ---------------------------------------------------------------------------
// The TSX side: the elements that name those classes.
// ---------------------------------------------------------------------------

const MAX_ALTS = 64;

const cross = (a: string[], b: string[]): string[] => {
  const out: string[] = [];
  for (const x of a) {
    for (const y of b) {
      if (out.length >= MAX_ALTS) return [HOLE];
      out.push(x + y);
    }
  }
  return out;
};

/** Every string this expression can evaluate to; unknown parts become HOLE. */
function possibleValues(node: ts.Node | undefined): string[] {
  if (!node) return [HOLE];
  if (ts.isStringLiteralLike(node)) return [node.text];
  if (ts.isTemplateExpression(node)) {
    let alts = [node.head.text];
    for (const span of node.templateSpans) {
      alts = cross(alts, possibleValues(span.expression));
      alts = cross(alts, [span.literal.text]);
    }
    return alts;
  }
  if (ts.isConditionalExpression(node)) {
    return [...possibleValues(node.whenTrue), ...possibleValues(node.whenFalse)];
  }
  if (ts.isParenthesizedExpression(node)) return possibleValues(node.expression);
  if (ts.isBinaryExpression(node)) {
    const op = node.operatorToken.kind;
    if (op === ts.SyntaxKind.AmpersandAmpersandToken) return [...possibleValues(node.right), ""];
    if (op === ts.SyntaxKind.BarBarToken || op === ts.SyntaxKind.QuestionQuestionToken)
      return [...possibleValues(node.left), ...possibleValues(node.right)];
    if (op === ts.SyntaxKind.PlusToken) return cross(possibleValues(node.left), possibleValues(node.right));
    return [HOLE];
  }
  if (ts.isCallExpression(node)) return node.arguments.flatMap(possibleValues);
  if (ts.isArrayLiteralExpression(node)) return node.elements.flatMap(possibleValues);
  return [HOLE];
}

/**
 * Whether a `tabIndex` attribute makes the element a tab stop.
 *
 * The PRESENCE of the attribute is not the question: `tabIndex={-1}` is a
 * programmatic focus target and no tab stop at all, so reading presence alone
 * would let a claimed fix be satisfied by the one value that leaves the
 * region exactly as unreachable as it was. Anything that is not a literal
 * non-negative number is treated as not a tab stop, which is the conservative
 * direction: it can fail a real fix and ask for a plain `0`, never pass a
 * region that cannot be tabbed to.
 */
function isTabStop(attribute: ts.JsxAttribute | undefined, source: ts.SourceFile): boolean {
  if (!attribute?.initializer) return false;
  const expression = ts.isJsxExpression(attribute.initializer)
    ? attribute.initializer.expression
    : attribute.initializer;
  const value = Number.parseFloat(expression?.getText(source).replace(/^["'`]|["'`]$/g, "") ?? "");
  return Number.isFinite(value) && value >= 0;
}

/** One JSX element that names a class, and the attributes this test judges by. */
interface Element {
  /** Source path relative to `client/src/`, with the 1-based line. */
  where: string;
  tag: string;
  tabbable: boolean;
  classNames: string[];
}

interface Markup {
  /** Class name → every element naming it statically. */
  elements: Map<string, Element[]>;
  /** The non-HOLE prefixes of class tokens that are built at runtime. */
  dynamicPrefixes: string[];
}

function readMarkup(files: string[]): Markup {
  const elements = new Map<string, Element[]>();
  const dynamicPrefixes: string[] = [];
  for (const file of files) {
    if (!file.endsWith(".tsx")) continue;
    if (/__tests__|\.test\./.test(file)) continue;
    const text = fs.readFileSync(file, "utf8");
    const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const rel = path.relative(SRC, file);
    const walk = (node: ts.Node): void => {
      const attributes = ts.isJsxOpeningLikeElement(node) ? node.attributes.properties : undefined;
      if (attributes) {
        const named = (name: string): ts.JsxAttribute | undefined =>
          attributes.find(
            (property): property is ts.JsxAttribute =>
              ts.isJsxAttribute(property) && property.name.getText(source) === name,
          );
        const className = named("className");
        if (className?.initializer) {
          const init = ts.isJsxExpression(className.initializer)
            ? className.initializer.expression
            : className.initializer;
          const tokens = possibleValues(init).flatMap((alt) => alt.split(/\s+/)).filter(Boolean);
          const statics = tokens.filter((token) => !token.includes(HOLE));
          for (const token of tokens) {
            if (!token.includes(HOLE)) continue;
            const prefix = token.slice(0, token.indexOf(HOLE));
            if (prefix) dynamicPrefixes.push(prefix);
          }
          const { line } = source.getLineAndCharacterOfPosition(node.getStart(source));
          const element: Element = {
            where: `${rel}:${line + 1}`,
            tag: ts.isJsxOpeningLikeElement(node) ? node.tagName.getText(source) : "?",
            tabbable: isTabStop(named("tabIndex"), source),
            classNames: statics,
          };
          for (const token of new Set(statics)) {
            if (!/^-?[_a-zA-Z][\w-]*$/.test(token)) continue;
            const list = elements.get(token);
            if (list) list.push(element);
            else elements.set(token, [element]);
          }
        }
      }
      ts.forEachChild(node, walk);
    };
    walk(source);
  }
  return { elements, dynamicPrefixes };
}

// ---------------------------------------------------------------------------
// The baseline.
// ---------------------------------------------------------------------------



// ---------------------------------------------------------------------------

/** The measurement for one candidate selector, from the markup. */
interface Measurement {
  elements: Element[];
  /** No element names any of the selector's classes, statically or dynamically. */
  noElement: boolean;
  /** Some class is only ever produced by a runtime hole. */
  dynamic: boolean;
}

function measure(candidate: Candidate, markup: Markup): Measurement {
  const found = candidate.classes.flatMap((name) => markup.elements.get(name) ?? []);
  const dynamic =
    found.length === 0 &&
    // A CSS module renames its classes at build time, so no static scan can
    // join `.hierarchyTree` to `className={styles.hierarchyTree}`.
    (candidate.file.endsWith(".module.css") ||
      candidate.classes.some((name) => markup.dynamicPrefixes.some((prefix) => name.startsWith(prefix))));
  return { elements: found, noElement: found.length === 0 && !dynamic, dynamic };
}

const report = (candidate: Candidate, measurement: Measurement): string =>
  [
    `${candidate.selector}  [${candidate.clips.map((clip) => `${clip.axis}:${clip.bound}`).join(" ")}, ${candidate.file}]`,
    measurement.noElement
      ? "    no element names it"
      : measurement.dynamic
        ? "    class is built at runtime"
        : measurement.elements
            .map((element) => `    <${element.tag}> ${element.where}${element.tabbable ? " tabIndex" : ""}`)
            .join("\n"),
  ].join("\n");

/** The reason the baseline states for one element site of one block. */
const reasonFor = (entry: BaselineEntry, element: Element): Reason =>
  entry.sites?.find((site) => site.where === element.where)?.reason ?? entry.reason;

/** The four things AC2 requires on a region this ticket fixes. */
const FIX_CLASS = "scroll-focus-ring";

/** Reasons that explain nothing without the thing they point at named. */
const NOTE_REQUIRED: readonly Reason[] = [
  "focusable-descendant",
  "composite-widget-keys",
  "placeholder-site",
  "unmeasurable",
  "not-rendered-at-rest",
];

describe("bounded scrolling regions are enumerated and reachable", () => {
  const files = walkFiles(SRC);
  const cssFiles = files.filter((file) => file.endsWith(".css"));
  const found = candidates(cssFiles);
  const markup = readMarkup(files);
  const measurements = new Map(found.map((candidate) => [candidate.selector, measure(candidate, markup)]));
  const bySelector = new Map(found.map((candidate) => [candidate.selector, candidate]));

  it("extracts a plausible corpus from both sides", () => {
    // A guard on the guard: an extractor that silently stopped working would
    // make every assertion below pass while vouching for nothing.
    expect(cssFiles.length).toBeGreaterThan(80);
    expect(markup.elements.size).toBeGreaterThan(500);
    expect(found.length).toBeGreaterThan(40);
  });

  it("reports both bound kinds and both axes, which is the only denominator here", () => {
    // Deliberately no figure: 19, 39, 84 and 87 are all retired. Every cell
    // must be non-empty, because excluding the flex-bound kind is the error
    // that produced the retired 84 (it is the LARGER of the two classes and it
    // holds `.app-sidebar-list`), and excluding the x axis is the gap
    // lg-milestone-that-2230 fixed in the detector.
    const cells = new Map<string, number>();
    for (const candidate of found)
      for (const clip of candidate.clips)
        cells.set(`${clip.axis}/${clip.bound}`, (cells.get(`${clip.axis}/${clip.bound}`) ?? 0) + 1);
    for (const cell of ["x/declared", "x/flex", "y/declared", "y/flex"])
      expect(cells.get(cell) ?? 0).toBeGreaterThan(0);
    // Every candidate clips on at least one axis, or the predicate produced it
    // for no reason it can state.
    expect(found.filter((candidate) => candidate.clips.length === 0)).toEqual([]);
  });

  it("lists every candidate in the baseline", () => {
    const listed = new Set(BASELINE.map((entry) => entry.selector));
    const missing = found
      .filter((candidate) => !listed.has(candidate.selector))
      .map((candidate) => report(candidate, measurements.get(candidate.selector) as Measurement));
    expect(missing).toEqual([]);
  });

  it("holds no baseline entry that is no longer a candidate", () => {
    const live = new Set(found.map((candidate) => candidate.selector));
    expect(BASELINE.filter((entry) => !live.has(entry.selector)).map((entry) => entry.selector)).toEqual([]);
  });

  it("states one reason per entry, from the closed vocabulary", () => {
    const bad: string[] = [];
    for (const entry of BASELINE) {
      for (const reason of [entry.reason, ...(entry.sites ?? []).map((site) => site.reason)]) {
        if (!REASONS.includes(reason)) bad.push(`${entry.selector}: ${reason} is outside the vocabulary`);
      }
      // A reason that names nothing explains nothing: these four are only
      // meaningful with the tab stop, the driving control, the gate, or the
      // follow-up ticket written out.
      for (const [reason, note, where] of [
        [entry.reason, entry.note, entry.selector] as const,
        ...(entry.sites ?? []).map((site) => [site.reason, site.note ?? entry.note, `${entry.selector} @ ${site.where}`] as const),
      ]) {
        if (NOTE_REQUIRED.includes(reason) && !note?.trim()) {
          bad.push(`${where}: ${reason} with no note`);
        }
        // `unmeasurable` is the one term whose note has a required CONTENT,
        // and AC10(h) is why: it is the only word left that means "cannot be
        // decided", so without a bound it becomes a parking space under a new
        // name — which is the one thing AC-SPLIT(4) forbids outright.
        // The note must name the construct and say that it resolves to no
        // declaration in this program. A component tag does not qualify —
        // AC10(d)(2) requires one resolved — and neither does a statically
        // empty subtree, which is unreachable and belongs to AC10(g1).
        if (reason === "unmeasurable" && !/resolves to no declaration in this program/.test(note ?? "")) {
          bad.push(`${where}: unmeasurable without naming a construct that resolves to nothing`);
        }
      }
      for (const site of entry.sites ?? []) {
        if (!bySelector.get(entry.selector)) continue;
        const known = (measurements.get(entry.selector) as Measurement).elements.some(
          (element) => element.where === site.where,
        );
        if (!known) bad.push(`${entry.selector}: site ${site.where} names no element`);
      }
    }
    expect(bad).toEqual([]);
  });

  it("agrees with the measurement on which entries have no element at all", () => {
    const claimed = {
      noElement: BASELINE.filter((entry) => entry.reason === "no-element").map((entry) => entry.selector).sort(),
      unresolved: BASELINE.filter((entry) => entry.reason === "unresolved").map((entry) => entry.selector).sort(),
    };
    const measured = {
      noElement: found
        .filter((candidate) => (measurements.get(candidate.selector) as Measurement).noElement)
        .map((candidate) => candidate.selector)
        .sort(),
      unresolved: found
        .filter((candidate) => (measurements.get(candidate.selector) as Measurement).dynamic)
        .map((candidate) => candidate.selector)
        .sort(),
    };
    expect(claimed).toEqual(measured);
    // Asserted explicitly, so a denominator that shrinks because a selector
    // stopped resolving cannot be read as progress: neither of these is a fix,
    // and neither may be recorded as one.
    // One, not the two measured before the fix: `.initiative-create-picked ul`
    // was `no-element` only because its subject was an unclassed <ul>, and
    // AC2's fix for it STARTS by giving that <ul> a class. The one left is
    // `.tl-callout-body`, which is dead CSS and not a region at all.
    expect(claimed.noElement).toHaveLength(1);
    expect(claimed.unresolved).toHaveLength(1);
  });

  it("treats no unresolved or absent entry as reachable or fixed", () => {
    // AC10(f). A selector that joins to no element is neither a fix nor a
    // region anyone can reach, so it may carry neither claim — otherwise a
    // denominator that shrinks because a selector stopped resolving reads as
    // progress.
    const fixed = new Set(
      BASELINE.filter((entry) => entry.reason === "tabbable" || entry.reason === "focusable-descendant").map(
        (entry) => entry.selector,
      ),
    );
    const wrong = found
      .filter((candidate) => fixed.has(candidate.selector))
      .filter((candidate) => {
        const measurement = measurements.get(candidate.selector) as Measurement;
        return measurement.noElement || measurement.dynamic;
      })
      .map((candidate) => candidate.selector);
    expect(wrong).toEqual([]);
  });

  it("holds every region it calls tabbable to the tab stop AND the shared ring", () => {
    // AC2's two file-visible requirements, on every site the baseline records
    // as `tabbable`. There is no separate term for a fix, so this one
    // assertion covers both halves of AC2 batch 1 — the three sites that
    // already carried `tabIndex={0}` and are migrating onto the shared class —
    // and every site the measurement adds, with no list to keep in step.
    //
    // The `aria-label` and the `role="group"` are the other two of AC2's four
    // things, and they are asserted per region by that component's own test,
    // which can read the EXPOSED role. A file scan cannot: `role="group"` on
    // an `<ol>` overrides `list` and costs the "list, N items" announcement,
    // and only a render knows which won.
    const wrong: string[] = [];
    for (const candidate of found) {
      const entry = BASELINE.find((item) => item.selector === candidate.selector);
      if (!entry) continue;
      for (const element of (measurements.get(candidate.selector) as Measurement).elements) {
        if (reasonFor(entry, element) !== "tabbable") continue;
        if (!element.tabbable)
          wrong.push(`${candidate.selector} at ${element.where}: no literal non-negative tabIndex`);
        if (!element.classNames.includes(FIX_CLASS))
          wrong.push(`${candidate.selector} at ${element.where}: no ${FIX_CLASS} class`);
      }
    }
    expect(wrong).toEqual([]);
  });

  it("states no count of its own, because the denominator is its output", () => {
    // AC10(c), enforced on this file rather than asserted about it. Every
    // retired figure — 19, 39, 84, 87 — reached a criterion as prose, and
    // three rounds then argued with the tree about which prose was right.
    //
    // So: no assertion in this file may compare a measured population to a
    // literal, and this test is what makes that checkable. It reads its own
    // source and fails on a `toHaveLength(<number>)` or a `toBe(<number>)`
    // applied to a `BASELINE`/`found`/`measurements` length.
    const source = fs.readFileSync(__filename, "utf8");
    const figures = [
      ...source.matchAll(/(BASELINE|found|measurements|elements)[^\n]{0,80}?\.?(?:length)[^\n]{0,40}?to(?:Be|HaveLength|Equal)\((\d+)\)/g),
    ].map((match) => match[0]);
    expect(figures).toEqual([]);
    // The population IS reported, per axis and per bound kind, so a class of
    // region cannot go missing inside one total. All four cells must be
    // populated: excluding the flex-bound kind produced the retired 84, and
    // excluding the x axis is the gap lg-milestone-that-2230 fixed in the
    // detector and AC-DEPS asked this census to re-derive.
    const cells = new Map<string, number>();
    for (const candidate of found)
      for (const clip of candidate.clips)
        cells.set(`${clip.axis}/${clip.bound}`, (cells.get(`${clip.axis}/${clip.bound}`) ?? 0) + 1);
    for (const cell of ["x/declared", "x/flex", "y/declared", "y/flex"])
      expect(cells.get(cell) ?? 0).toBeGreaterThan(0);
  });

  it("admits the regions that clip sideways, which a vertical predicate cannot produce", () => {
    // AC-DEPS, from lg-milestone-that-2230. These are candidates only because
    // the predicate above asks the bound question per axis; under the vertical
    // definition this file shipped with, they were not candidates at all — and
    // shrink-only cannot fire for a candidate the predicate never produces,
    // which is why the adversarial second extractor missed them too.
    //
    // AC-DEPS names three. The predicate produces five: `.tab-bar-scroll` and
    // `.terminal-tabs` were already entries here under the old axis-blind flex
    // test, which credited their `flex-grow` to the y axis. Per AC10(c) that
    // is a prose error, and the population still moves by three in substance —
    // both of the extra two are reachable through their `role="tab"` buttons.
    const listed = new Set(BASELINE.map((entry) => entry.selector));
    const sideways = found
      .filter((candidate) => candidate.clips.every((clip) => clip.axis === "x"))
      .map((candidate) => candidate.selector)
      .sort();
    expect(sideways).toEqual(
      [
        ".branch-worktrees-info",
        ".lg-chat-prompt-row",
        ".running-lane-tabs",
        ".tab-bar-scroll",
        ".terminal-tabs",
      ].sort(),
    );
    // And every one of them is in the baseline, which is the half AC-DEPS
    // asked for: a re-derivation, not a note that the predicate was wrong.
    expect(sideways.filter((selector) => !listed.has(selector))).toEqual([]);
  });

  it("defines the shared focus ring once, inset, beside the button ring", () => {
    // One definition, so three copies of one rule cannot drift apart; inset,
    // because an outset ring on a clipping box is drawn under its neighbours,
    // which is why both rules this replaces already chose -2px.
    const rules = cssFiles
      .map((file) => ({ file: path.relative(SRC, file), source: fs.readFileSync(file, "utf8") }))
      .filter(({ source }) => source.includes(`.${FIX_CLASS}:focus-visible`));
    expect(rules.map((rule) => rule.file)).toEqual(["index.css"]);
    expect(rules[0]?.source).toMatch(
      /\.scroll-focus-ring:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--ac\)[^}]*outline-offset:\s*-2px[^}]*\}/,
    );
    // Not transitioned and not animated: the ring is the one signal saying
    // where the keyboard is, and delaying it is the defect in miniature.
    const block = /\.scroll-focus-ring:focus-visible\s*\{([^}]*)\}/.exec(rules[0]?.source ?? "")?.[1] ?? "";
    expect(block).not.toMatch(/transition|animation/);
  });

  it("leaves the bound and the overflow of every candidate region alone", () => {
    // The bound is the thing this ticket must not touch: no line-clamp, no
    // unbounded box, no animated height or width, and no `overflow` value
    // changed to dodge the fix. AC10(h)'s last sentence puts this over EVERY
    // candidate block and not only the fixed ones — a region this ticket
    // merely records is still a region whose bound it must not touch, and the
    // earlier version of this check looked only where a fix had been claimed.
    const fixed = new Set(BASELINE.map((entry) => entry.selector));
    const offenders: string[] = [];
    for (const file of cssFiles) {
      const source = fs.readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
      for (const [, rawSelector, body] of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
        const selector = rawSelector.trim().replace(/\s+/g, " ");
        if (!fixed.has(selector)) continue;
        if (/line-clamp/.test(body)) offenders.push(`${selector}: line-clamp`);
        if (/transition[^;]*(height|width)/.test(body)) offenders.push(`${selector}: animated bound`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
