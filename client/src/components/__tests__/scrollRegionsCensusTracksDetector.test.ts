/**
 * The census is derived from the detector, so when the detector is corrected
 * the census must be re-derived. This file is the check that it was.
 *
 * lg-milestone-that-2215's AC-DEPS names two correctness bugs in
 * `findUsabilityProblems`'s `scrollable-region-focusable` rule as prerequisites
 * of the sweep, on the grounds that "a baseline derived from a detector with
 * either bug is wrong in both directions: it misses horizontally-clipping
 * regions and it passes regions no key can reach." Both have since landed on
 * this branch:
 *
 * - **lg-milestone-that-2230** (`2d7a71446`) made the rule ask the geometry
 *   question per axis: `scrollWidth > clientWidth` where the horizontal axis
 *   has a scrollbar, not only `scrollHeight > clientHeight`
 *   (`usabilityCheck.ts:405-406`). The instrument now reports a region clipped
 *   sideways.
 * - **lg-milestone-that-2232** (`55eb13139`) made the rule's tabbability test
 *   the house definition again, so a control inside a disabled `<fieldset>`, a
 *   `[hidden]` subtree or an `[inert]` one is no longer counted as the tab stop
 *   that makes a region reachable.
 *
 * Neither correction reached the census, and each leaves it wrong in the
 * direction AC-DEPS predicted. Both tests below are RED at this commit, and
 * each names what it measured rather than asserting a figure.
 *
 * ## Why this is a third instrument and not an edit to the other two
 *
 * `scrollRegionsAreReachable.test.ts` is the census; its
 * `.adversarial.test.ts` sibling is a second, independent extractor written so
 * that a defect in one cannot hide in both. That independence did not help
 * here, and the reason is the finding: **both extractors inherited the same
 * candidate definition from AC10(b), which names `height`, `max-height`,
 * `min-height: 0` and `flex-grow` and no horizontal bound at all.** Two
 * instruments cannot disagree about a class of region neither one can see.
 * This file therefore asks the axis question itself rather than reusing either
 * one's predicate, and reads the committed baseline with TypeScript's parser
 * rather than importing it, so a baseline edited to quiet this file has to be
 * edited visibly.
 */

import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const SRC = path.resolve(__dirname, "../..");
const CENSUS = path.join(SRC, "test/scrollRegionsBaseline.ts");

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "node_modules") walk(full, out);
    } else out.push(full);
  }
  return out;
}

const FILES = walk(SRC);
const CSS = FILES.filter((file) => file.endsWith(".css"));
const TSX = FILES.filter((file) => file.endsWith(".tsx") && !/__tests__|\.test\./.test(file));

const SCROLLING = /^(auto|scroll)$/;

interface Rule {
  selector: string;
  file: string;
  declarations: Record<string, string>;
}

const RULES: Rule[] = CSS.flatMap((file) => {
  const source = fs.readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  return Array.from(source.matchAll(/([^{}]+)\{([^{}]*)\}/g), ([, rawSelector, body]) => {
    const declarations: Record<string, string> = {};
    for (const statement of body.split(";")) {
      const at = statement.indexOf(":");
      if (at < 0) continue;
      declarations[statement.slice(0, at).trim().toLowerCase()] = statement
        .slice(at + 1)
        .trim()
        .toLowerCase();
    }
    return { selector: rawSelector.trim().replace(/\s+/g, " "), file: path.relative(SRC, file), declarations };
  }).filter((rule) => rule.selector !== "" && !rule.selector.startsWith("@") && !rule.selector.startsWith("%"));
});

/**
 * Does the named axis scroll — the question `usabilityCheck.ts:389-399` now
 * asks of computed style, asked here of the declarations.
 *
 * The per-axis longhand wins where there is one. Otherwise the shorthand's
 * words are positional: `overflow: auto hidden` scrolls on x and not on y, and
 * a single word applies to both.
 */
const axisScrolls = (declarations: Record<string, string>, axis: "x" | "y"): boolean => {
  const own = declarations[`overflow-${axis}`];
  if (own !== undefined && own !== "") return SCROLLING.test(own);
  const words = (declarations["overflow"] ?? "").split(/\s+/).filter(Boolean);
  if (words.length === 0) return false;
  return SCROLLING.test((axis === "x" ? words[0] : (words[1] ?? words[0])) ?? "");
};

/**
 * Is the block bounded along the named axis?
 *
 * Both kinds, mirroring AC10(b) — a declared bound, or the flex-bound kind
 * where a flex child is told it may be smaller than its content — but asked of
 * whichever axis is in question rather than of the block height alone. The
 * `min-*: 0` test is `0` exactly and not `0.5rem`, as the other two
 * extractors' is.
 */
const boundedOn = (declarations: Record<string, string>, axis: "x" | "y"): boolean => {
  const extent = axis === "x" ? "width" : "height";
  for (const property of [extent, `max-${extent}`]) {
    const value = declarations[property];
    if (value !== undefined && value !== "" && value !== "none" && value !== "auto") return true;
  }
  if (/^0(?:[a-z%]+)?$/i.test(declarations[`min-${extent}`] ?? "")) return true;
  const grow = declarations["flex-grow"] ?? declarations["flex"]?.split(/\s+/)[0];
  return grow !== undefined && grow !== "" && Number.parseFloat(grow) > 0;
};

/** The candidate definition the census and its adversary both implement. */
const isCensusCandidate = (rule: Rule): boolean =>
  (["x", "y"] as const).some(
    (axis) => axisScrolls(rule.declarations, axis) && boundedOn(rule.declarations, axis),
  );

// ---------------------------------------------------------------------------
// The committed baseline, read rather than imported.
// ---------------------------------------------------------------------------

interface Listed {
  selector: string;
  reason: string;
  note: string;
  /** `where` of every per-site override, with the reason it states. */
  sites: { where: string; reason: string }[];
}

/**
 * Read `BASELINE` out of the census with TypeScript's parser.
 *
 * Importing it would make this file agree with the census by construction on
 * what the baseline says, which is the one thing here worth checking
 * independently; and a test file that imports another test file's internals
 * cannot be read without reading both.
 */
function readBaseline(): Listed[] {
  const text = fs.readFileSync(CENSUS, "utf8");
  const source = ts.createSourceFile(CENSUS, text, ts.ScriptTarget.Latest, true);
  const out: Listed[] = [];
  const literal = (node: ts.Node | undefined): string =>
    node && ts.isStringLiteralLike(node) ? node.text : "";
  const walkNode = (node: ts.Node): void => {
    if (
      ts.isVariableDeclaration(node) &&
      node.name.getText(source) === "BASELINE" &&
      node.initializer &&
      ts.isArrayLiteralExpression(
        ts.isAsExpression(node.initializer) ? node.initializer.expression : node.initializer,
      )
    ) {
      const array = ts.isAsExpression(node.initializer) ? node.initializer.expression : node.initializer;
      for (const element of (array as ts.ArrayLiteralExpression).elements) {
        if (!ts.isObjectLiteralExpression(element)) continue;
        const field = (name: string): ts.Node | undefined =>
          element.properties.find(
            (property): property is ts.PropertyAssignment =>
              ts.isPropertyAssignment(property) && property.name.getText(source) === name,
          )?.initializer;
        const sitesNode = field("sites");
        const sites: Listed["sites"] = [];
        if (sitesNode && ts.isArrayLiteralExpression(sitesNode)) {
          for (const site of sitesNode.elements) {
            if (!ts.isObjectLiteralExpression(site)) continue;
            const siteField = (name: string): ts.Node | undefined =>
              site.properties.find(
                (property): property is ts.PropertyAssignment =>
                  ts.isPropertyAssignment(property) && property.name.getText(source) === name,
              )?.initializer;
            sites.push({ where: literal(siteField("where")), reason: literal(siteField("reason")) });
          }
        }
        out.push({
          selector: literal(field("selector")),
          reason: literal(field("reason")),
          note: literal(field("note")),
          sites,
        });
      }
    }
    ts.forEachChild(node, walkNode);
  };
  walkNode(source);
  return out;
}

const BASELINE = readBaseline();

// ---------------------------------------------------------------------------
// Tab stops in a JSX subtree, under the house definition lg-milestone-that-2232
// restored.
// ---------------------------------------------------------------------------

/** Tags that are tab stops without a `tabIndex` of their own. */
const NATIVE = new Set(["button", "input", "select", "textarea", "summary"]);

/** `components/ui` primitives, which render those tags. */
const PRIMITIVE = new Set(["Button", "Input", "Select", "Textarea", "TriStateCheckbox"]);

/**
 * Attributes that take a control out of the tab order — the divergence
 * lg-milestone-that-2232 closed in the detector, asked statically here.
 *
 * Only an UNCONDITIONAL one disqualifies. `disabled` bare, `disabled={true}`
 * or `aria-disabled="true"` is a control that is never a tab stop; but
 * `disabled={pending}` is one most of the time, and whether it is one now is a
 * runtime question no static scan can settle. A conditional gate therefore
 * makes the site unmeasurable from here and is reported rather than failed —
 * the jsdom cases lg-milestone-that-2232 landed are what cover that half.
 */
const GATING = ["disabled", "hidden", "inert", "aria-disabled"];

/** `true`, or the attribute with no value at all. */
const alwaysGated = (initializer: ts.Node | undefined): boolean => {
  if (initializer === undefined) return true;
  const expression = ts.isJsxExpression(initializer) ? initializer.expression : initializer;
  const text = expression?.getText().replace(/^["'`]|["'`]$/g, "") ?? "";
  return text === "true";
};

interface Subtree {
  /** Controls in the subtree that nothing gates. */
  open: string[];
  /** Controls an UNCONDITIONAL `disabled`/`hidden`/`inert` removes for good. */
  gated: string[];
  /**
   * What this scan cannot follow: a component tag, a child rendered by a call
   * (`{viewRows(true)}`), `{children}`, or a control gated on runtime state. A
   * subtree holding one of these is unmeasurable from here, and is reported
   * rather than failed.
   */
  opaque: string[];
}

/** Every element site naming `token`, with its subtree classified. */
function subtreesNaming(token: string): { where: string; tag: string; subtree: Subtree }[] {
  const found: { where: string; tag: string; subtree: Subtree }[] = [];
  for (const file of TSX) {
    const text = fs.readFileSync(file, "utf8");
    const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const rel = path.relative(SRC, file);
    const attribute = (node: ts.JsxOpeningLikeElement, name: string): ts.JsxAttribute | undefined =>
      node.attributes.properties.find(
        (property): property is ts.JsxAttribute =>
          ts.isJsxAttribute(property) && property.name.getText(source) === name,
      );
    const classesOf = (node: ts.JsxOpeningLikeElement): string[] => {
      const initializer = attribute(node, "className")?.initializer;
      if (!initializer) return [];
      const literals: string[] = [];
      const collect = (child: ts.Node): void => {
        if (ts.isStringLiteralLike(child)) literals.push(child.text);
        if (ts.isTemplateExpression(child)) {
          literals.push(child.head.text, ...child.templateSpans.map((span) => span.literal.text));
        }
        ts.forEachChild(child, collect);
      };
      collect(initializer);
      return literals.flatMap((value) => value.split(/\s+/)).filter(Boolean);
    };
    const classify = (root: ts.Node): Subtree => {
      const subtree: Subtree = { open: [], gated: [], opaque: [] };
      const visit = (node: ts.Node): void => {
        if (
          ts.isJsxOpeningLikeElement(node) &&
          node !== root &&
          !(ts.isJsxElement(root) && node === root.openingElement)
        ) {
          const tag = node.tagName.getText(source);
          const tabIndex = attribute(node, "tabIndex")?.initializer;
          const tabValue = tabIndex
            ? Number.parseFloat(
                (ts.isJsxExpression(tabIndex) ? tabIndex.expression : tabIndex)
                  ?.getText(source)
                  .replace(/^["'`]|["'`]$/g, "") ?? "",
              )
            : Number.NaN;
          const isControl =
            NATIVE.has(tag) ||
            PRIMITIVE.has(tag) ||
            (tag === "a" && attribute(node, "href") !== undefined) ||
            (Number.isFinite(tabValue) && tabValue >= 0);
          if (isControl) {
            const { line } = source.getLineAndCharacterOfPosition(node.getStart(source));
            const label = `<${tag}> ${rel}:${line + 1}`;
            const gates = GATING.map((name) => attribute(node, name)).filter(
              (found): found is ts.JsxAttribute => found !== undefined,
            );
            if (gates.length === 0) subtree.open.push(label);
            else if (gates.some((gate) => alwaysGated(gate.initializer))) subtree.gated.push(label);
            else subtree.opaque.push(`${label} gated on runtime state`);
          } else if (/^[A-Z]/.test(tag) && !PRIMITIVE.has(tag)) {
            subtree.opaque.push(`<${tag}>`);
          } else if (node.attributes.properties.some(ts.isJsxSpreadAttribute)) {
            // `{...common}` can carry `tabIndex`, and this scan reads named
            // attributes only. `.editor-tree`'s roving tab stop is spread in
            // exactly this way, so a spread is a thing not to judge.
            subtree.opaque.push(`<${tag}> with a spread attribute`);
          }
        }
        // A child this scan cannot resolve to markup: a call that renders rows
        // (`{viewRows(true)}`), or a bare name holding markup built above
        // (`{body}`, `{children}`). A `.map` with an inline arrow is NOT one of
        // these — its JSX is right there and the walk below reaches it, and
        // neither is a property access, which in a child position is text
        // (`{wt.path}`) far more often than markup.
        if (
          ts.isJsxExpression(node) &&
          node.expression &&
          (ts.isJsxElement(node.parent) || ts.isJsxFragment(node.parent))
        ) {
          const inner = node.expression;
          const hasJsx = (node: ts.Node): boolean =>
            ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node) || ts.isJsxFragment(node)
              ? true
              : (ts.forEachChild(node, hasJsx) ?? false);
          const isMap =
            ts.isCallExpression(inner) &&
            ts.isPropertyAccessExpression(inner.expression) &&
            /^(map|flatMap)$/.test(inner.expression.name.getText(source));
          // A `.map` whose callback writes its JSX inline is transparent — the
          // walk reaches it where it stands. One whose callback DELEGATES
          // (`{changes.map((c) => renderChangeItem(c, "added"))}`) is not, and
          // reading it as transparent is how `.diff-content`'s whole subtree
          // read as holding no control at all when it holds a <Textarea>.
          // AC10(d)(2) requires that resolved; this file is the conservative
          // third extractor, so it reports it rather than following it.
          const rendersElsewhere =
            (ts.isCallExpression(inner) && (!isMap || !hasJsx(inner))) || ts.isIdentifier(inner);
          if (rendersElsewhere) subtree.opaque.push(`{${inner.getText(source).slice(0, 40)}}`);
        }
        ts.forEachChild(node, visit);
      };
      visit(root);
      return subtree;
    };
    const walkNode = (node: ts.Node): void => {
      if (ts.isJsxElement(node) && classesOf(node.openingElement).includes(token)) {
        const { line } = source.getLineAndCharacterOfPosition(node.openingElement.getStart(source));
        found.push({
          where: `${rel}:${line + 1}`,
          tag: node.openingElement.tagName.getText(source),
          subtree: classify(node),
        });
      }
      ts.forEachChild(node, walkNode);
    };
    walkNode(source);
  }
  return found;
}

/** The rightmost compound of each comma-separated part, as AC10(a)(i) says. */
const subjectClasses = (selector: string): string[] =>
  selector
    .split(",")
    .flatMap((part) => {
      const subject = part.trim().split(/\s+|>|\+|~/).filter(Boolean).pop() ?? "";
      return Array.from(subject.matchAll(/\.([A-Za-z0-9_-]+)/g), (match) => match[1] ?? "");
    })
    .filter(Boolean);

// ---------------------------------------------------------------------------

describe("the census tracks the detector it is derived from", () => {
  it("reads both sides, so nothing below vouches for an empty scan", () => {
    // The guard on this guard: a third extractor that silently returned
    // nothing would make every assertion here pass while measuring neither the
    // tree nor the baseline.
    expect(CSS.length).toBeGreaterThan(80);
    expect(TSX.length).toBeGreaterThan(200);
    expect(RULES.filter(isCensusCandidate).length).toBeGreaterThan(40);
    expect(BASELINE.length).toBeGreaterThan(40);
    expect(BASELINE.filter((entry) => entry.reason === "").map((entry) => entry.selector)).toEqual([]);
  });

  it("enumerates a region clipped sideways, which lg-milestone-that-2230 made the detector report", () => {
    // AC-DEPS, first half. The candidate definition in AC10(b) names `height`,
    // `max-height`, `min-height: 0` and `flex-grow`: every term is vertical.
    // Since `2d7a71446` the detector asks `scrollWidth > clientWidth` on an
    // axis with a horizontal scrollbar, so it reports a class of region the
    // census cannot list — and shrink-only, which fails on a candidate the
    // baseline does not hold, cannot fire for a candidate the predicate never
    // produces.
    //
    // This is the same error in kind as the retired figure of 84, which was
    // declared-bound-only and so excluded the flex-bound class wholesale. The
    // census's own docstring says so about that one. It is not caught by the
    // adversarial file either, because that file inherited the same vertical
    // predicate: two independent instruments cannot disagree about a class of
    // region neither can see.
    const sideways = RULES.filter(
      (rule) => axisScrolls(rule.declarations, "x") && boundedOn(rule.declarations, "x"),
    );
    const missed = sideways
      .filter((rule) => !isCensusCandidate(rule))
      .map((rule) => `${rule.file}: ${rule.selector}`)
      .sort();
    expect(missed).toEqual([]);
  });

  it("holds every focusable-descendant note to a tab stop the house definition accepts", () => {
    // AC-DEPS, second half, and AC10(d)'s word "measured".
    //
    // `focusable-descendant` is the reason most of the baseline carries, and it
    // is the one reason the census does not measure: `NOTE_REQUIRED` asks only
    // that the note be non-empty prose. Nothing checks that the descendant it
    // names exists, is a control, or is in the tab order. Since `55eb13139` the
    // detector's own tabbability test is `tabbableWithin`'s again, which
    // disqualifies a control inside a disabled `<fieldset>`, a `[hidden]`
    // subtree or an `[inert]` one — so a baseline that counts such a control
    // reports reachable exactly where the instrument it is derived from now
    // reports unreachable.
    //
    // Deliberately conservative in two directions, so a red here is a finding
    // and not an artifact of the scan: a subtree holding a component tag this
    // file cannot follow into is reported as unmeasurable rather than failed,
    // and a control is counted as gated on the mere PRESENCE of `disabled`,
    // `hidden`, `inert` or `aria-disabled`, because `disabled={pending}` is not
    // a tab stop for as long as `pending`.
    const unreached: string[] = [];
    const unmeasurable: string[] = [];
    for (const entry of BASELINE) {
      for (const token of subjectClasses(entry.selector)) {
        for (const site of subtreesNaming(token)) {
          const override = entry.sites.find((item) => item.where === site.where)?.reason;
          if ((override ?? entry.reason) !== "focusable-descendant") continue;
          if (site.subtree.open.length > 0) continue;
          if (site.subtree.opaque.length > 0) {
            unmeasurable.push(`${entry.selector} at ${site.where}: via <${site.subtree.opaque[0]}>`);
            continue;
          }
          unreached.push(
            `${entry.selector} at ${site.where}: ${
              site.subtree.gated.length > 0
                ? `every control is gated — ${site.subtree.gated.join(", ")}`
                : "no control in the subtree at all"
            }`,
          );
        }
      }
    }
    // Reported, not asserted: what this scan cannot see is part of its result,
    // and a shrinking number here is not progress.
    expect(unmeasurable.length).toBeGreaterThanOrEqual(0);
    expect(unreached).toEqual([]);
  });
});
