/**
 * Every bounded scrolling region, on BOTH axes, with its reachability
 * MEASURED rather than asserted in prose.
 *
 * ## What this file is, and why it is a third instrument
 *
 * `scrollRegionsAreReachable.test.ts` is lg-milestone-that-2215's census and
 * `scrollRegionsAreReachable.adversarial.test.ts` is a second extractor
 * written to share no code with it, so that a defect in one could not hide in
 * both. That independence did not help, and the reason is the finding
 * test-break recorded: **both implement AC10(b), which named `height`,
 * `max-height`, `min-height: 0` and `flex-grow` — every term vertical.** Two
 * instruments cannot disagree about a class of region neither one can see, and
 * shrink-only cannot fire for a candidate the predicate never produces. That
 * is the same shape as the retired figure of 84, which was declared-bound-only
 * and so excluded the flex-bound class wholesale.
 *
 * This file is the amended AC10(b)/(c): the geometry question asked **per
 * axis**, as `usabilityCheck.ts:405-406` has asked it since
 * lg-milestone-that-2230 (`2d7a71446`), with the counts reported per axis and
 * per bound kind so a class of region cannot go missing inside one total.
 *
 * ## Why it builds a TypeScript Program and not just a parser
 *
 * AC10(d) requires `focusable-descendant` to be MEASURED, and AC10(h) forbids
 * `unmeasurable` for a site whose subtree is statically empty. Those two rules
 * meet on one shape a parser alone cannot split:
 *
 *     <pre className="prepared-action-output">{output}</pre>     // text
 *     <ul  className="app-sidebar-list">{viewRows(true)}</ul>    // markup
 *
 * Both are one element with one expression child and no element descendants.
 * The first is a keyboard-unreachable output pane; the second is a list of
 * links AC5 forbids touching. Nothing in the syntax separates them — only the
 * types do, and they separate them exactly: `string` against
 * `(Element | null)[]`. So this file asks the checker, which costs about three
 * seconds for the whole client and removes the entire judgement call.
 *
 * A consequence worth stating: an expression whose JSX is written INLINE
 * (a ternary, a `&&`, a `.map` with an arrow) is transparent, because the walk
 * below reaches that JSX where it stands. Only an identifier, a call or a
 * property access renders markup built somewhere this walk cannot follow.
 *
 * ## What it asserts
 *
 * AC-FOLD's invariant, which does not depend on whose list of regions is
 * right: **every element site this file measures as keyboard-unreachable
 * carries a literal non-negative `tabIndex` and the shared
 * `scroll-focus-ring` class.** The set is computed here, so it cannot be
 * narrowed by editing a list, and a region that stops being unreachable leaves
 * the set by measurement rather than by relabelling.
 */

import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const CLIENT = path.resolve(__dirname, "../../..");
const SRC = path.join(CLIENT, "src");

/** The shared ring class AC2 defines once in `index.css`. */
const FIX_CLASS = "scroll-focus-ring";

/**
 * The two reason strings AC10(d) deletes, and the constants that served them.
 *
 * Spelled by construction rather than written out, because the criterion is
 * that the strings appear NOWHERE in the four census files — and a test
 * asserting that, with the strings in it, fails itself. One meant "unreachable,
 * and this ticket does not fix it" (AC-FOLD); the other named this ticket as
 * the fixer, which was never a measured property of anything.
 */
const FORBIDDEN_TERMS = [
  ["de", "ferred"].join(""),
  ["DE", "FERRED_BLOCKS"].join(""),
  ["DE", "FERRED_TO"].join(""),
  ["fixed-by-", "2215"].join(""),
  ["FIXED", "_BY"].join(""),
];

// ---------------------------------------------------------------------------
// The CSS side: a candidate is bounded on an axis it actually scrolls.
// ---------------------------------------------------------------------------

type Axis = "x" | "y";
type BoundKind = "declared" | "flex";

interface Rule {
  selector: string;
  file: string;
  declarations: Record<string, string>;
}

interface Candidate extends Rule {
  /** Every axis that both scrolls and is bounded; a block may clip on both. */
  clips: { axis: Axis; bound: BoundKind }[];
  /** The classes of the selector's SUBJECT — AC10(a)(i). */
  classes: string[];
}

const SCROLLING = /^(auto|scroll)$/;

function walkFiles(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "node_modules") walkFiles(full, out);
    } else out.push(full);
  }
  return out;
}

/**
 * Does the named axis scroll?
 *
 * The per-axis longhand wins where there is one. Otherwise the shorthand's
 * words are positional: `overflow: auto hidden` scrolls on x and not on y, and
 * a single word applies to both.
 */
const axisScrolls = (declarations: Record<string, string>, axis: Axis): boolean => {
  const own = declarations[`overflow-${axis}`];
  if (own !== undefined && own !== "") return SCROLLING.test(own);
  const words = (declarations["overflow"] ?? "").split(/\s+/).filter(Boolean);
  if (words.length === 0) return false;
  return SCROLLING.test((axis === "x" ? words[0] : (words[1] ?? words[0])) ?? "");
};

/** A declared extent on this axis, with a value that actually bounds it. */
const declaredOn = (declarations: Record<string, string>, axis: Axis): boolean => {
  const extent = axis === "x" ? "width" : "height";
  return [extent, `max-${extent}`].some((property) => {
    const value = declarations[property];
    return value !== undefined && value !== "" && value !== "none" && value !== "auto";
  });
};

/** A flex child told it may be smaller than its content, on this axis. */
const flexOn = (declarations: Record<string, string>, axis: Axis): boolean => {
  const extent = axis === "x" ? "width" : "height";
  if (/^0(?:[a-z%]+)?$/i.test(declarations[`min-${extent}`] ?? "")) return true;
  const grow = declarations["flex-grow"] ?? declarations["flex"]?.split(/\s+/)[0];
  return grow !== undefined && grow !== "" && Number.parseFloat(grow) > 0;
};

/** The rightmost compound of each comma-separated part — the styled element. */
const subjectClasses = (selector: string): string[] => {
  const classes = new Set<string>();
  for (const part of selector.split(",")) {
    const subject = part.trim().split(/[\s>+~]+/).filter(Boolean).pop() ?? "";
    for (const [, name] of subject.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)) classes.add(name);
  }
  return [...classes];
};

function readCandidates(cssFiles: string[]): Candidate[] {
  const found: Candidate[] = [];
  for (const file of cssFiles) {
    const source = fs.readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    // Innermost blocks only: the pattern forbids braces inside, so an `@media`
    // wrapper is skipped and the rules within it are counted.
    for (const [, rawSelector, body] of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const selector = rawSelector.trim().replace(/\s+/g, " ");
      if (!selector || selector.startsWith("@") || selector.startsWith("%")) continue;
      const declarations: Record<string, string> = {};
      for (const statement of body.split(";")) {
        const at = statement.indexOf(":");
        if (at < 0) continue;
        declarations[statement.slice(0, at).trim().toLowerCase()] = statement
          .slice(at + 1)
          .trim()
          .toLowerCase();
      }
      const clips: Candidate["clips"] = [];
      for (const axis of ["x", "y"] as const) {
        if (!axisScrolls(declarations, axis)) continue;
        if (declaredOn(declarations, axis)) clips.push({ axis, bound: "declared" });
        else if (flexOn(declarations, axis)) clips.push({ axis, bound: "flex" });
      }
      if (clips.length === 0) continue;
      found.push({
        selector,
        file: path.relative(SRC, file),
        declarations,
        clips,
        classes: subjectClasses(selector),
      });
    }
  }
  return found;
}

// ---------------------------------------------------------------------------
// The TSX side, type-checked, with a component tag RESOLVED rather than
// surrendered to — AC10(d)(2).
// ---------------------------------------------------------------------------

/** Tags that are tab stops with no `tabIndex` of their own. */
const NATIVE_CONTROLS = new Set(["button", "input", "select", "textarea", "summary"]);

/** `components/ui` primitives and routing links, which render those tags. */
const PRIMITIVE_CONTROLS = new Set([
  "Button",
  "Input",
  "Select",
  "Textarea",
  "TriStateCheckbox",
  "IconCloseButton",
  "Link",
  "NavLink",
]);

/**
 * Roles a composite widget's own rows carry, which are driven by a control
 * OUTSIDE the region rather than by being tab stops themselves — AC10(d)(3).
 */
const COMPOSITE_CHILD_ROLES = new Set(["option", "treeitem", "tab", "menuitem", "row", "gridcell"]);

/** Attributes that can take a control out of the tab order. */
const GATING = ["disabled", "hidden", "inert", "aria-disabled"];

/** A type that can hold React markup rather than text. */
const MARKUP_TYPE = /\b(Element|ReactElement|ReactNode|ReactPortal)\b/;

/**
 * Does this expression's TYPE say it carries markup?
 *
 * The whole reason this file builds a `ts.Program`: `<pre>{output}</pre>` and
 * `<ul>{viewRows(true)}</ul>` are one shape to a parser and `string` against
 * `(Element | null)[]` to the checker.
 */
const isMarkupTyped = (checker: ts.TypeChecker, node: ts.Node): boolean =>
  MARKUP_TYPE.test(checker.typeToString(checker.getTypeAtLocation(node)));

/**
 * How deep the walk follows component declarations.
 *
 * The visited set already makes the walk terminate; this only keeps a
 * pathological chain from costing more than the question is worth. A region's
 * own tab stop is never twelve components away, and a chain that deep would
 * report `unmeasurable` with the construct named rather than guess.
 */
const MAX_RESOLUTION_DEPTH = 12;

/** What one element site's subtree holds, classified. */
interface Subtree {
  /**
   * Controls that are in the tab order.
   *
   * A control gated on runtime state (`disabled={isPending}`) counts, per
   * AC10(d)(2): conservatism has a direction, and for a gate the safe
   * direction is "assume it is a tab stop". The opposite choice reports most
   * of the app's modals as unreachable, and a conformance test nobody trusts
   * is the failure mode this ticket is about.
   */
  open: string[];
  /** Controls an UNCONDITIONAL gate removes from the tab order for good. */
  gated: string[];
  /**
   * Markup this walk cannot follow into, AFTER trying to resolve it.
   *
   * AC10(h): only a construct that resolves to no declaration in this program
   * belongs here — `{children}`, a bare prop-typed name, a spread attribute.
   * A component tag is not one of them, because (d)(2) requires it resolved.
   */
  opaque: string[];
  /** Rows of a composite widget, which are not tab stops by design. */
  composite: string[];
  /** Element descendants, however classified. */
  descendants: number;
  /** The class lists of the immediate children, for the skeleton test. */
  childClasses: string[][];
  /** Declarations this walk followed, for the note and for the audit. */
  followed: string[];
}

interface Site {
  /** Source path relative to `client/src/`, with the 1-based line. */
  where: string;
  tag: string;
  classes: string[];
  tabbable: boolean;
  ring: boolean;
  ariaBusy: boolean;
  role?: string;
  subtree: Subtree;
}

const sourceOf = (node: ts.Node): ts.SourceFile => node.getSourceFile();

const attribute = (node: ts.JsxOpeningLikeElement, name: string): ts.JsxAttribute | undefined =>
  node.attributes.properties.find(
    (property): property is ts.JsxAttribute =>
      ts.isJsxAttribute(property) && property.name.getText(sourceOf(property)) === name,
  );

const attributeText = (found: ts.JsxAttribute | undefined): string | undefined => {
  if (!found?.initializer) return undefined;
  const expression = ts.isJsxExpression(found.initializer) ? found.initializer.expression : found.initializer;
  return expression?.getText(sourceOf(found)).replace(/^["'`]|["'`]$/g, "");
};

/** Only a literal non-negative number is a tab stop; `tabIndex={-1}` is not. */
const isTabStop = (found: ts.JsxAttribute | undefined): boolean => {
  const value = Number.parseFloat(attributeText(found) ?? "");
  return Number.isFinite(value) && value >= 0;
};

const classesOf = (node: ts.JsxOpeningLikeElement): string[] => {
  const initializer = attribute(node, "className")?.initializer;
  if (!initializer) return [];
  const literals: string[] = [];
  const collect = (child: ts.Node): void => {
    if (ts.isStringLiteralLike(child)) literals.push(child.text);
    if (ts.isTemplateExpression(child))
      literals.push(child.head.text, ...child.templateSpans.map((span) => span.literal.text));
    ts.forEachChild(child, collect);
  };
  collect(initializer);
  return literals.flatMap((value) => value.split(/\s+/)).filter(Boolean);
};

/** `components/Foo.tsx:91`, the one form every note and every key uses. */
const at = (node: ts.Node): string => {
  const source = sourceOf(node);
  return `${path.relative(SRC, source.fileName)}:${
    source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1
  }`;
};

/**
 * The declarations a JSX tag, or a markup-typed name, resolves to in THIS
 * program.
 *
 * This is the whole of AC10(d)(2). The alternative — a vocabulary term for
 * "a component tag means the scan cannot prove it" — would have covered eight
 * element sites this ticket exists to fix, and AC-SPLIT(4) forbids a reason
 * string that means "this ticket does not fix it" under any spelling.
 *
 * An empty result is the honest answer and the ONLY thing that may still read
 * `unmeasurable`: `{children}` resolves to a parameter, a bare prop-typed name
 * to a property signature, and a spread attribute to nothing nameable at all.
 */
function resolveToDeclarations(checker: ts.TypeChecker, node: ts.Node): ts.Node[] {
  let symbol = checker.getSymbolAtLocation(node);
  if (symbol && symbol.flags & ts.SymbolFlags.Alias) symbol = checker.getAliasedSymbol(symbol);
  const declarations = symbol?.declarations ?? [];
  const bodies: ts.Node[] = [];
  for (const declaration of declarations) {
    if (!sourceOf(declaration).fileName.startsWith(SRC)) continue;
    // A parameter, a destructured binding of one, or a property signature is
    // the shape AC10(h) names: the markup is supplied from outside this
    // program's reach, so there is nothing to follow and `unmeasurable` is the
    // honest answer rather than a surrender.
    if (ts.isParameter(declaration) || ts.isPropertySignature(declaration)) continue;
    if (ts.isBindingElement(declaration) && bindsAParameter(declaration)) continue;
    if (ts.isFunctionDeclaration(declaration)) {
      if (declaration.body) bodies.push(declaration.body);
      continue;
    }
    // A class component's markup is in its `render`, which the walk below
    // reaches from the class body like any other node.
    if (ts.isClassDeclaration(declaration)) {
      bodies.push(declaration);
      continue;
    }
    if (ts.isVariableDeclaration(declaration)) {
      if (declaration.initializer) bodies.push(declaration.initializer);
      // `let body;` followed by `body = <p .../>` in each branch is this
      // codebase's own idiom for a five-state render — `MemoryPage.tsx:65`,
      // `AppSidebarRunning.tsx:80`, `ReferenceAutoRereview.tsx:116`. The name
      // DOES resolve to a declaration in this program, so AC10(h) does not
      // allow `unmeasurable` for it; what it resolves to is every assignment.
      else bodies.push(...assignmentsTo(checker, symbol, declaration));
      continue;
    }
    if (ts.isFunctionExpression(declaration) || ts.isArrowFunction(declaration)) {
      bodies.push(declaration.body);
    }
  }
  return bodies;
}

/** Is this destructuring a parameter (`const { Chat } = spec`) rather than a value? */
function bindsAParameter(binding: ts.BindingElement): boolean {
  let node: ts.Node = binding;
  while (ts.isBindingElement(node) || ts.isObjectBindingPattern(node) || ts.isArrayBindingPattern(node))
    node = node.parent;
  if (ts.isParameter(node)) return true;
  if (ts.isVariableDeclaration(node) && node.initializer)
    return ts.isIdentifier(node.initializer) || ts.isPropertyAccessExpression(node.initializer);
  return false;
}

/** Every `name = <expression>` assigned to an uninitialised local. */
function assignmentsTo(
  checker: ts.TypeChecker,
  symbol: ts.Symbol | undefined,
  declaration: ts.VariableDeclaration,
): ts.Node[] {
  if (!symbol) return [];
  let scope: ts.Node = declaration;
  while (scope.parent && !ts.isFunctionLike(scope) && !ts.isSourceFile(scope)) scope = scope.parent;
  const assigned: ts.Node[] = [];
  const look = (node: ts.Node): void => {
    if (
      ts.isBinaryExpression(node) &&
      node.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
      ts.isIdentifier(node.left) &&
      checker.getSymbolAtLocation(node.left) === symbol
    )
      assigned.push(node.right);
    ts.forEachChild(node, look);
  };
  look(scope);
  return assigned;
}

/** Is JSX written INSIDE this expression, where the walk already reaches it? */
function containsJsx(node: ts.Node): boolean {
  if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node) || ts.isJsxFragment(node)) return true;
  return ts.forEachChild(node, containsJsx) ?? false;
}

/** Resolve the thing a markup-bearing call ultimately names. */
const calleeOf = (expression: ts.Expression): ts.Node => {
  if (ts.isCallExpression(expression)) return calleeOf(expression.expression as ts.Expression);
  return expression;
};

/**
 * Every name a markup-typed call could have built its markup through.
 *
 * `{rows.map(renderOption)}` and `{changes.map((c) => renderChangeItem(c))}`
 * both resolve their CALLEE to `Array.prototype.map` in `lib.es5.d.ts`, which
 * is outside this program and so reads as "resolves to no declaration" — the
 * one thing AC10(h) still allows `unmeasurable` for. The markup is in the
 * ARGUMENT, and `.topbar-search-popover`'s `role="option"` rows and
 * `.diff-content`'s whole subtree are both only reachable through it.
 */
const markupTargetsOf = (call: ts.CallExpression): ts.Node[] => [
  calleeOf(call.expression),
  ...call.arguments.filter(
    (argument) => ts.isIdentifier(argument) || ts.isPropertyAccessExpression(argument),
  ),
];

/**
 * One element site's subtree, measured — following component declarations.
 *
 * `root` is the element at the site. The walk covers the JSX written at the
 * site AND, for every component tag or markup-typed name inside it, the JSX
 * the resolved declaration returns, bounded by `visited` so a component that
 * renders itself (or two that render each other) terminates.
 */
function measureSubtree(checker: ts.TypeChecker, root: ts.JsxElement | ts.JsxSelfClosingElement): Subtree {
  const subtree: Subtree = {
    open: [],
    gated: [],
    opaque: [],
    composite: [],
    descendants: 0,
    childClasses: [],
    followed: [],
  };
  const rootOpening = ts.isJsxElement(root) ? root.openingElement : root;
  const visited = new Set<ts.Node>([root]);

  const follow = (owner: string, targets: ts.Node[], depth: number): void => {
    if (depth > MAX_RESOLUTION_DEPTH) {
      subtree.opaque.push(`${owner} past ${MAX_RESOLUTION_DEPTH} resolutions`);
      return;
    }
    const bodies = targets.flatMap((target) => resolveToDeclarations(checker, target));
    if (bodies.length === 0) {
      // The ONLY road to `unmeasurable`: nothing this construct names has a
      // declaration in this program. AC10(h) requires the note to say so, and
      // `owner` is how it says it.
      subtree.opaque.push(owner);
      return;
    }
    for (const body of bodies) {
      if (visited.has(body)) continue;
      visited.add(body);
      subtree.followed.push(`${owner} -> ${at(body)}`);
      visit(body, depth + 1, false);
    }
  };

  const visit = (node: ts.Node, depth: number, atSite: boolean): void => {
    if (ts.isJsxOpeningLikeElement(node) && node !== rootOpening) {
      subtree.descendants += 1;
      if (atSite && (node.parent === root || node.parent?.parent === root))
        subtree.childClasses.push(classesOf(node));
      const tag = node.tagName.getText(sourceOf(node));
      const label = `<${tag}> ${at(node)}`;
      const role = attributeText(attribute(node, "role"));
      if (role !== undefined && COMPOSITE_CHILD_ROLES.has(role)) subtree.composite.push(label);
      const isControl =
        NATIVE_CONTROLS.has(tag) ||
        PRIMITIVE_CONTROLS.has(tag) ||
        (tag === "a" && attribute(node, "href") !== undefined) ||
        isTabStop(attribute(node, "tabIndex"));
      if (isControl) {
        const gates = GATING.map((name) => attribute(node, name)).filter(
          (found): found is ts.JsxAttribute => found !== undefined,
        );
        const unconditional = gates.some((gate) => {
          const text = attributeText(gate);
          return text === undefined || text === "true";
        });
        if (gates.length === 0) subtree.open.push(label);
        else if (unconditional) subtree.gated.push(label);
        else subtree.open.push(`${label} (gated on runtime state)`);
      } else if (/^[A-Z]/.test(tag) || tag.includes(".")) {
        // AC10(d)(2): resolve the tag to its declaration and keep walking.
        follow(label, [node.tagName], depth);
      }
      if (node.attributes.properties.some(ts.isJsxSpreadAttribute)) {
        // `{...common}` can carry `tabIndex`, and nothing here reads an object
        // literal spread back. `.editor-tree`'s roving tab stop is spread in
        // exactly this way, so a spread is a thing not to judge — and it is
        // one of AC10(h)'s three legal constructs.
        subtree.opaque.push(`<${tag}> with a spread attribute, ${at(node)}`);
      }
    }
    if (
      ts.isJsxExpression(node) &&
      node.expression &&
      (ts.isJsxElement(node.parent) || ts.isJsxFragment(node.parent))
    ) {
      // Inline JSX — a ternary, a `&&`, a `.map` with an arrow — is
      // transparent, and `containsJsx` is what makes the docstring's claim
      // true rather than merely stated: `{rows.map((r) => <li .../>)}` is a
      // CALL, so the syntactic test alone sent it to be resolved, `.map`
      // resolved to `lib.es5` and the site read `unmeasurable` while its JSX
      // sat three lines below in plain sight. That one line is most of the
      // difference between 71 unmeasurable sites and the handful left.
      //
      // A name, a call or a property access with NO JSX inside it renders
      // markup built elsewhere, and only the TYPE says whether that is markup
      // or the text of an output pane. Where it IS markup, (d)(2) resolves it
      // the same way it resolves a tag.
      const inner = node.expression;
      const rendersElsewhere =
        !containsJsx(inner) &&
        !ts.isCallExpression(inner) &&
        (ts.isIdentifier(inner) || ts.isPropertyAccessExpression(inner));
      if (rendersElsewhere && isMarkupTyped(checker, inner)) {
        follow(`{${inner.getText(sourceOf(inner)).slice(0, 34)}} ${at(node)}`, [inner], depth);
      }
    }
    if (ts.isCallExpression(node) && !containsJsx(node) && isMarkupTyped(checker, node)) {
      // A call that returns markup and writes none of it inline. This is
      // checked ANYWHERE in the walk and not only as an element's child,
      // because `{changes.map((c) => renderChangeItem(c, "added"))}` buries
      // the render one arrow deep and `.diff-content`'s entire subtree is
      // behind it.
      follow(
        `${node.getText(sourceOf(node)).replace(/\s+/g, " ").slice(0, 44)} ${at(node)}`,
        markupTargetsOf(node),
        depth,
      );
    }
    ts.forEachChild(node, (child) => visit(child, depth, atSite));
  };

  visit(root, 0, true);
  return subtree;
}

/**
 * Every element site that names a class, with its subtree measured.
 *
 * One pass over the program, because building it is the expensive part.
 */
function readSites(program: ts.Program): Map<string, Site[]> {
  const checker = program.getTypeChecker();
  const byClass = new Map<string, Site[]>();

  for (const source of program.getSourceFiles()) {
    const file = source.fileName;
    if (!file.startsWith(SRC) || !file.endsWith(".tsx")) continue;
    if (/__tests__|\.test\./.test(file)) continue;

    const walkNode = (node: ts.Node): void => {
      const element = ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node) ? node : undefined;
      if (element) {
        const open = ts.isJsxElement(element) ? element.openingElement : element;
        const classes = classesOf(open);
        if (classes.length > 0) {
          const site: Site = {
            where: at(open),
            tag: open.tagName.getText(source),
            classes,
            tabbable: isTabStop(attribute(open, "tabIndex")),
            ring: classes.includes(FIX_CLASS),
            ariaBusy: attributeText(attribute(open, "aria-busy")) === "true",
            role: attributeText(attribute(open, "role")),
            subtree: measureSubtree(checker, element),
          };
          for (const token of new Set(classes)) {
            const list = byClass.get(token);
            if (list) list.push(site);
            else byClass.set(token, [site]);
          }
        }
      }
      ts.forEachChild(node, walkNode);
    };
    walkNode(source);
  }
  return byClass;
}

// ---------------------------------------------------------------------------
// The verdict for one element site — AC10(d)'s eight terms, less the three a
// CSS-side join decides (`no-element`, `unresolved`) and the one that is not a
// verdict at all (`not-rendered-at-rest` is a note on an entry, not a
// measurement of a subtree). The two terms round 3 and round 4 deleted are
// gone, and the assertion below checks they are gone from all four files.
// ---------------------------------------------------------------------------

type Verdict =
  | "focusable-descendant"
  | "tabbable"
  | "composite-widget-keys"
  | "placeholder-site"
  | "unmeasurable"
  | "unreachable";

/**
 * A fixed placeholder rather than the bounded content the rule exists for —
 * AC10(d)(5). Read from the conventions this codebase states for its own
 * loading, empty and error states rather than from a list of sites:
 * `aria-busy`, `role="alert"`, a `-empty` class, or children that are all
 * skeletons. Fixing one would ship exactly the dead tab stop AC8 forbids.
 */
const isPlaceholder = (site: Site): boolean =>
  site.ariaBusy ||
  site.role === "alert" ||
  site.classes.some((token) => /(^|-)empty$/.test(token)) ||
  (site.subtree.descendants > 0 &&
    site.subtree.childClasses.length > 0 &&
    site.subtree.childClasses.every((classes) => classes.some((token) => token.includes("skeleton"))));

const verdictFor = (site: Site): Verdict => {
  if (site.subtree.open.length > 0) return "focusable-descendant";
  if (site.tabbable) return "tabbable";
  if (site.subtree.composite.length > 0) return "composite-widget-keys";
  if (isPlaceholder(site)) return "placeholder-site";
  if (site.subtree.opaque.length > 0) return "unmeasurable";
  // AC10(h): nothing in or under it can take focus, and no construct is
  // standing in the way of having read that. A statically empty subtree is
  // unreachable, where empty means no element descendant AND no markup-typed
  // expression child — the qualification that keeps AC5's four sidebar lists
  // out of the fix set, since `{viewRows(true)}` now resolves to the links.
  return "unreachable";
};

// ---------------------------------------------------------------------------

/** One candidate block joined to the elements that name it. */
interface Measured {
  candidate: Candidate;
  sites: { site: Site; verdict: Verdict }[];
}

const describe_ = (measured: Measured, only?: Verdict): string =>
  [
    `${measured.candidate.selector}  [${measured.candidate.clips
      .map((clip) => `${clip.axis}:${clip.bound}`)
      .join(" ")}, ${measured.candidate.file}]`,
    ...measured.sites
      .filter((entry) => only === undefined || entry.verdict === only)
      .map(
        (entry) =>
          `    ${entry.verdict.padEnd(21)} <${entry.site.tag}> ${entry.site.where}` +
          `${entry.site.tabbable ? " tabIndex" : ""}${entry.site.ring ? ` .${FIX_CLASS}` : ""}`,
      ),
  ].join("\n");

describe("bounded scrolling regions, measured on both axes", () => {
  const cssFiles = walkFiles(SRC).filter((file) => file.endsWith(".css"));
  const candidates = readCandidates(cssFiles);

  const config = ts.readConfigFile(path.join(CLIENT, "tsconfig.app.json"), ts.sys.readFile);
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, CLIENT);
  const program = ts.createProgram(parsed.fileNames, { ...parsed.options, noEmit: true });
  const sitesByClass = readSites(program);

  const measured: Measured[] = candidates.map((candidate) => {
    const sites = [
      ...new Map(
        candidate.classes
          .flatMap((token) => sitesByClass.get(token) ?? [])
          .map((site) => [site.where, site]),
      ).values(),
    ];
    return { candidate, sites: sites.map((site) => ({ site, verdict: verdictFor(site) })) };
  });

  const everySite = measured.flatMap((entry) => entry.sites);
  const unreachable = measured.flatMap((entry) =>
    entry.sites.filter(({ verdict }) => verdict === "unreachable").map((site) => ({ ...site, entry })),
  );

  it("reads both sides, so nothing below vouches for an empty scan", () => {
    // A guard on the guard: an extractor that silently stopped working would
    // make every assertion below pass while measuring nothing at all.
    expect(cssFiles.length).toBeGreaterThan(80);
    expect(parsed.fileNames.length).toBeGreaterThan(400);
    expect(sitesByClass.size).toBeGreaterThan(500);
    expect(candidates.length).toBeGreaterThan(40);
    expect(everySite.length).toBeGreaterThan(100);
  });

  it("separates an output pane from a list of links by its type, not its shape", () => {
    // The reason this file builds a Program, pinned on the two sites that
    // forced it. Both are one element with one expression child and NO element
    // descendants:
    //
    //     <pre className="prepared-action-output">{output}</pre>   // string
    //     <ul  className="app-sidebar-list">{viewRows(true)}</ul>  // Element[]
    //
    // A scan that cannot tell them apart must either excuse an unreachable
    // output pane or demand the four dead tab stops AC5 forbids on the app's
    // primary navigation. There is no third option, and nothing in the syntax
    // decides it.
    //
    // The pane's verdict is `tabbable` now that AC2 fixed it, so the property
    // pinned here is the one the fix did not change: its SUBTREE still holds
    // no tab stop — `{output}` is a string and always was — where the
    // sidebar's resolves to four links. That is the distinction the Program
    // buys, and unlike the pre-fix verdict it is stable across the fix.
    const pane = everySite.find(
      ({ site }) => site.where === "components/PreparedActionPanel.tsx:107",
    );
    expect(pane).toBeDefined();
    expect(pane?.site.subtree.open).toEqual([]);
    expect(pane?.site.subtree.opaque).toEqual([]);
    const sidebar = everySite.filter(({ site }) => site.classes.includes("app-sidebar-list"));
    expect(sidebar.length).toBe(4);
    expect(sidebar.map(({ verdict }) => verdict)).not.toContain("unreachable");
    // AC5, on the elements: no tab stop and no ring on any of the four, in
    // either direction, whatever the detector reports about them.
    for (const { site } of sidebar) {
      expect(site.tabbable).toBe(false);
      expect(site.ring).toBe(false);
    }
  });

  it("reports the counts per axis and per bound kind, so a class cannot go missing in a total", () => {
    // AC10(c). Deliberately no figure for the total: 19, 39, 84 and 87 are all
    // retired, and the denominator is this test's output. What IS asserted is
    // that all four cells are populated — excluding the flex-bound kind is the
    // error that produced the retired 84, and excluding the x axis is the one
    // lg-milestone-that-2230 corrected in the detector.
    const cells = new Map<string, number>();
    for (const candidate of candidates)
      for (const clip of candidate.clips)
        cells.set(`${clip.axis}/${clip.bound}`, (cells.get(`${clip.axis}/${clip.bound}`) ?? 0) + 1);
    for (const cell of ["x/declared", "x/flex", "y/declared", "y/flex"])
      expect(cells.get(cell) ?? 0).toBeGreaterThan(0);
  });

  it("enumerates the regions that clip sideways, which nothing vertical can see", () => {
    // AC10(b)'s horizontal term, named by AC-DEPS from lg-milestone-that-2230.
    // All five are flex-bound on x, which is why a predicate asking only about
    // height could not produce them as candidates at all — and so shrink-only
    // could not fire for them either.
    //
    // AC-DEPS names three. The predicate produces five: `.tab-bar-scroll` and
    // `.terminal-tabs` were already baseline entries under the OLD axis-blind
    // flex test, which credited their `flex-grow` to the y axis. Per AC10(c)
    // the count in prose is the error, not this list, and the population still
    // moves by three in substance — both of the extra two are reachable
    // through their `role="tab"` buttons.
    const sideways = candidates
      .filter((candidate) => candidate.clips.some((clip) => clip.axis === "x"))
      .filter((candidate) => !candidate.clips.some((clip) => clip.axis === "y"))
      .map((candidate) => candidate.selector)
      .sort();
    expect(sideways).toEqual(
      [".branch-worktrees-info", ".lg-chat-prompt-row", ".running-lane-tabs", ".tab-bar-scroll", ".terminal-tabs"].sort(),
    );
  });

  it("classifies every element site, and leaves none unexplained", () => {
    const unclassified = everySite.filter(({ verdict }) => verdict === undefined);
    expect(unclassified).toEqual([]);
    // Each class must have members, or the verdict function has a dead branch
    // that is vouching for nothing.
    const tally = new Map<Verdict, number>();
    for (const { verdict } of everySite) tally.set(verdict, (tally.get(verdict) ?? 0) + 1);
    // `unreachable` is deliberately NOT in this list: AC10(g1) is satisfied
    // exactly when that class is empty, so asserting it has members would be
    // asserting the fix set was never emptied. Its branch is covered by the
    // mutation guards below, which keep it reachable in principle.
    for (const verdict of ["focusable-descendant", "tabbable", "unmeasurable"] as const)
      expect(tally.get(verdict) ?? 0).toBeGreaterThan(0);
  });

  it("names the construct behind every site it calls unmeasurable", () => {
    // AC10(h), which bounds the one term meaning "cannot be decided" so it
    // cannot become a parking space under a new name: each one must name the
    // SPECIFIC static construct that blocks the measurement, and this file
    // produces that name rather than accepting prose for it.
    //
    // AC10(h) says the term may not cover a subtree "statically empty of
    // descendants", because such a site is unreachable and belongs to the fix
    // set. Measured, that sentence needs one word it does not have: empty of
    //ELEMENT descendants is not the same as empty. Six sites have no element
    // descendant and are still not decidable —
    //
    //     <ul className="app-sidebar-list">{viewRows(true)}</ul>
    //     <div className="memory-page-body">{body}</div>
    //
    // — because the expression child is typed `Element`, so the markup exists
    // and this walk simply cannot reach it. A site is empty, and therefore
    // unreachable, when it holds neither an element descendant NOR a
    // markup-typed expression child. That is the refinement the type checker
    // buys, and it is what keeps AC5's four sidebar lists out of the fix set.
    const unnamed = everySite
      .filter(({ verdict }) => verdict === "unmeasurable")
      .filter(({ site }) => site.subtree.opaque.length === 0 && /^[a-z]/.test(site.tag))
      .map(({ site }) => `${site.where}: <${site.tag}> unmeasurable with no construct named`);
    expect(unnamed).toEqual([]);
    // And the named construct is one of AC10(h)'s four kinds, every time.
    const strange = everySite
      .filter(({ verdict }) => verdict === "unmeasurable")
      .filter(({ site }) => /^[a-z]/.test(site.tag))
      .filter(({ site }) => !site.subtree.opaque.some((named) => /^[<{]/.test(named)))
      .map(({ site }) => `${site.where}: ${site.subtree.opaque.join(", ")}`);
    expect(strange).toEqual([]);
  });

  it("records a placeholder only where there is a placeholder to record", () => {
    // AC10(d)(5), measured: each one must be a branch this codebase's own
    // conventions mark as loading, empty or error — `aria-busy`, `role=alert`,
    // a `-empty` class, or children that are all skeletons — and must hold no
    // control and no collection. A labelled box in front of a skeleton is the
    // dead tab stop AC8 forbids, which is why these are recorded and not fixed.
    const placeholders = everySite.filter(({ verdict }) => verdict === "placeholder-site");
    expect(placeholders.length).toBeGreaterThan(0);
    for (const { site } of placeholders) {
      expect(site.subtree.open).toEqual([]);
      expect(isPlaceholder(site)).toBe(true);
    }
  });

  it("computes an EMPTY set of candidate sites holding no tab stop — AC10(g1)", () => {
    // AC-FOLD in its measurable form, and the red-to-green contract of this
    // file. The set is COMPUTED here, not listed, so no edit to a list can
    // narrow it, and a region that stops being unreachable leaves it by
    // measurement rather than by relabelling.
    //
    // There is deliberately no term left to carry an unreachable site:
    // the term meaning "unreachable and not fixed" went with AC-FOLD, and the
    // one naming this ticket as the fixer went with round 4, because a fixed
    // element carries `tabIndex={0}` and therefore measures `tabbable`, which
    // is what a fix IS. So an unfixed site FAILS here. It
    // cannot be recorded, and this assertion is the whole of AC2's fix set.
    const holdingNoTabStop = unreachable.map(
      ({ entry, site }) => `${describe_(entry, "unreachable")}\n      -> <${site.tag}> ${site.where}`,
    );
    expect(holdingNoTabStop.sort()).toEqual([]);
  });

  it("puts a literal tabIndex and the shared ring on exactly what it fixed — AC10(g2)", () => {
    // The anti-WIDENING half, and the reason (g1) can be stated as "empty"
    // without inviting a fix sprinkled over regions the census never marked.
    //
    // The question is about the SUBTREE, not the element: an element's own
    // `tabIndex` is not inside it, so the three sites that already carried one
    // before this ticket pass unchanged and need no exemption. That is what
    // round 3's single assertion got wrong — it asked whether a site was
    // unreachable BEFORE the fix, which no scan of the post-fix tree can
    // answer, so it could only ever be satisfied by a hand-maintained list.
    //
    // A region whose only control is gated on runtime state is reachable under
    // AC10(d)(2) and so fixing it is a widening caught here:
    // `.lg-chat-prompt-row` (`<Button disabled={disabled}>`) and
    // `.diff-content` (`<Textarea disabled={...}>`) are both in this class.
    // That is a defect FAMILY — lg-milestone-that-2415 — not an instance of
    // this one.
    const widened = everySite
      .filter(({ site }) => site.ring || (site.tabbable && site.classes.includes(FIX_CLASS)))
      .filter(({ site }) => site.subtree.open.length > 0)
      .map(
        ({ site, verdict }) =>
          `<${site.tag}> ${site.where} [${verdict}] carries .${FIX_CLASS} but its subtree holds ` +
          `${site.subtree.open.join(", ")}`,
      );
    expect(widened.sort()).toEqual([]);
    // And the ring never travels without the tab stop it exists to draw: a
    // ring with no `tabIndex` is a rule that can never fire, which is the
    // silent half-fix this criterion is here to catch.
    const ringWithoutStop = everySite
      .filter(({ site }) => site.ring && !site.tabbable)
      .map(({ site }) => `<${site.tag}> ${site.where} has .${FIX_CLASS} and no literal tabIndex`);
    expect(ringWithoutStop.sort()).toEqual([]);
  });

  it("leaves the four app-sidebar-list navigation lists alone, and names their tab stop", () => {
    // AC5, asserted on the ELEMENTS. Adding `tabIndex={0}` here would insert
    // four dead tab stops ahead of the app's primary navigation, and AC5
    // forbids it "whatever the detector reports about them" — the detector
    // fired on these 24 times before lg-milestone-that-2223.
    //
    // The positive half is new in round 4 and is what AC10(d)(2)'s declaration
    // resolution bought: AC5 requires the entry to NAME the tab stop, and
    // until the walk followed `{viewRows(true)}` and `{body}` to their
    // declarations there was no name to give. All four now resolve to a
    // `<NavLink>` or a `<Link>` in `appSidebarRows.tsx` / the row map, which
    // is the difference between measuring reachability and asserting it.
    const sidebar = everySite.filter(({ site }) => site.classes.includes("app-sidebar-list"));
    expect(sidebar.length).toBe(4);
    for (const { site, verdict } of sidebar) {
      expect(verdict).toBe("focusable-descendant");
      expect(site.subtree.open.length).toBeGreaterThan(0);
      expect(site.tabbable).toBe(false);
      expect(site.ring).toBe(false);
    }
  });

  it("holds neither deleted reason string, under any spelling", () => {
    // AC-FOLD and AC10(d), checked over the four census files rather than
    // over this one, because a term deleted from the vocabulary in one
    // instrument and still carried in another is not deleted.
    const censusFiles = [
      "scrollRegionsAreReachable.test.ts",
      "scrollRegionsAreReachable.adversarial.test.ts",
      "scrollRegionsCensusTracksDetector.test.ts",
      "scrollRegionsMeasuredReachability.test.ts",
    ];
    const offenders: string[] = [];
    for (const name of censusFiles) {
      const source = fs.readFileSync(path.join(__dirname, name), "utf8");
      for (const term of FORBIDDEN_TERMS)
        if (source.includes(term)) offenders.push(`${name}: ${term}`);
    }
    expect(offenders).toEqual([]);
  });



  it("puts the shared ring in one place, inset, beside the button ring", () => {
    // One definition, so copies of one rule cannot drift apart; inset, because
    // an outset ring on a clipping box is drawn under its neighbours, which is
    // why both rules AC2 deletes already chose -2px.
    const rules = cssFiles
      .map((file) => ({ file: path.relative(SRC, file), source: fs.readFileSync(file, "utf8") }))
      .filter(({ source }) => source.includes(`.${FIX_CLASS}:focus-visible`));
    expect(rules.map((rule) => rule.file)).toEqual(["index.css"]);
    expect(rules[0]?.source).toMatch(
      /\.scroll-focus-ring:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--ac\)[^}]*outline-offset:\s*-2px[^}]*\}/,
    );
    // Not transitioned: the ring is the one signal saying where the keyboard
    // is, and delaying it is this ticket's own defect in miniature.
    const block = /\.scroll-focus-ring:focus-visible\s*\{([^}]*)\}/.exec(rules[0]?.source ?? "")?.[1] ?? "";
    expect(block).not.toMatch(/transition|animation/);
  });

  it("leaves the bound and the overflow of every candidate alone", () => {
    // AC10(h)'s last sentence: the check covers every candidate block, not
    // only the fixed ones. A region this ticket merely records is still a
    // region whose bound it must not touch.
    const selectors = new Set(candidates.map((candidate) => candidate.selector));
    const offenders: string[] = [];
    for (const file of cssFiles) {
      const source = fs.readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
      for (const [, rawSelector, body] of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
        const selector = rawSelector.trim().replace(/\s+/g, " ");
        if (!selectors.has(selector)) continue;
        if (/line-clamp/.test(body)) offenders.push(`${selector}: line-clamp`);
        if (/transition[^;]*(height|width)/.test(body)) offenders.push(`${selector}: animated bound`);
      }
    }
    expect(offenders).toEqual([]);
  });

  // -------------------------------------------------------------------------
  // The fix set can be EMPTIED without fixing anything, and these three
  // assertions are what stops it. Found by test-break, each one demonstrated
  // against the tree before it was written.
  //
  // (g1) asks whether any site still measures unreachable. That is a question
  // about the RESIDUE, so every way of making a site measure something else
  // answers it without touching the region. Three relabellings do, and all
  // three are one line:
  //
  //   9.  A tab stop on a DESCENDANT rather than on the scroll element —
  //       `<pre className="studio-version-body"><span tabIndex={0} />` — or,
  //       the honest-mistake version of the same thing, a `Copy` control moved
  //       INSIDE the region it serves. The subtree then holds a tab stop, so
  //       the verdict is `focusable-descendant` and the site leaves the set.
  //   10. `role="alert"` on the pane, which satisfies `isPlaceholder` on its
  //       own and reads `placeholder-site` — a reason AC8 says is NOT fixed.
  //   11. A spread attribute on any descendant, which is one of AC10(h)'s
  //       three legal constructs, so the site reads `unmeasurable` with a
  //       note this file itself accepts as well-formed.
  //
  // Measured: each one moved a site out of the set, added no failure, and left
  // NOTHING asking about it — not (g1), not the baseline's tab-stop-and-ring
  // check, not the per-site AC3/AC8/AC9 coverage gate, which keys off the
  // baseline reason and so stops demanding tests for a site the moment the
  // baseline is regenerated from the mutated measurement. Regenerating is the
  // sanctioned action, so the evasion breaks no stated rule.
  //
  // What closes it is the one thing AC10(g)'s preamble says a post-fix scan
  // cannot have: the PRE-FIX measurement, frozen. These three literals are
  // that snapshot, generated from this file's own output at `c7f90df13` and
  // shrink-only in the manner of AC10(e)'s baseline — a site leaves by a
  // visible edit to a committed list, not by acquiring a new verdict.

  /**
   * Every site the fix set was computed from, measured at `c7f90df13`, paired
   * with the line that site sits at now.
   *
   * The pair is not decoration. A one-column list keyed on `file:line` cannot
   * survive the fix it is guarding, because four of the fixes insert lines
   * ABOVE their own site (AC8's substitute states) and every site below them
   * in the same file moves. Eight of the twenty-six did, and a frozen list
   * that reads `no longer a measured site` for them says nothing about
   * whether they were fixed or quietly dropped — which is the failure this
   * list exists to make impossible.
   *
   * So the LEFT column is the frozen pre-fix identity and never changes; the
   * RIGHT column is re-derived when a fix moves a site, and is the key the
   * verdict is read under. Deleting a row is still the only way out, and it
   * is still a visible edit to a committed literal.
   */
  const FIX_SET_AT_FREEZE: { frozen: string; now: string }[] = [
    { frozen: "components/BranchCheckoutConfirmModal.tsx:46", now: "components/BranchCheckoutConfirmModal.tsx:46" },
    { frozen: "components/BranchWorktreesModal.tsx:84", now: "components/BranchWorktreesModal.tsx:84" },
    { frozen: "components/DeleteTicketConfirmModal.tsx:46", now: "components/DeleteTicketConfirmModal.tsx:46" },
    { frozen: "components/DeleteViewConfirmModal.tsx:44", now: "components/DeleteViewConfirmModal.tsx:44" },
    { frozen: "components/ImportTicketsModal.tsx:207", now: "components/ImportTicketsModal.tsx:207" },
    { frozen: "components/PreparedActionPanel.tsx:107", now: "components/PreparedActionPanel.tsx:107" },
    { frozen: "components/StageFanoutReview.tsx:170", now: "components/StageFanoutReview.tsx:183" },
    { frozen: "components/UsageModal.tsx:160", now: "components/UsageModal.tsx:172" },
    { frozen: "components/chat/ComposerNotes.tsx:34", now: "components/chat/ComposerNotes.tsx:34" },
    { frozen: "components/chat/LiveThinkingStream.tsx:75", now: "components/chat/LiveThinkingStream.tsx:77" },
    { frozen: "components/chat/primitives/AgentPrimitive.tsx:86", now: "components/chat/primitives/AgentPrimitive.tsx:86" },
    { frozen: "components/chat/primitives/TerminalPrimitive.tsx:36", now: "components/chat/primitives/TerminalPrimitive.tsx:40" },
    { frozen: "components/dashboard/TicketTimeline.tsx:454", now: "components/dashboard/TicketTimeline.tsx:455" },
    { frozen: "components/dashboard/TicketTimeline.tsx:503", now: "components/dashboard/TicketTimeline.tsx:513" },
    { frozen: "components/initiatives/plan/RedraftConfirmModal.tsx:40", now: "components/initiatives/plan/RedraftConfirmModal.tsx:40" },
    { frozen: "components/livingMap/LivingMap.tsx:264", now: "components/livingMap/LivingMap.tsx:264" },
    { frozen: "components/machine/DiskCleanupModal.tsx:216", now: "components/machine/DiskCleanupModal.tsx:216" },
    { frozen: "components/machine/DiskCleanupModal.tsx:291", now: "components/machine/DiskCleanupModal.tsx:298" },
    { frozen: "components/machine/DiskFoldersModal.tsx:131", now: "components/machine/DiskFoldersModal.tsx:131" },
    { frozen: "components/studio/AgentDeleteConfirm.tsx:60", now: "components/studio/AgentDeleteConfirm.tsx:60" },
    { frozen: "components/studio/AgentVersionHistory.tsx:95", now: "components/studio/AgentVersionHistory.tsx:98" },
    { frozen: "components/studio/GateCommandList.tsx:254", now: "components/studio/GateCommandList.tsx:255" },
    { frozen: "components/views/primitives/queueLanePrimitive.tsx:82", now: "components/views/primitives/queueLanePrimitive.tsx:82" },
    { frozen: "components/workspaceDashboard/MergeQueueSection.tsx:278", now: "components/mergeQueue/MergeQueueRow.tsx:190" },
    { frozen: "components/workspaces/WorkspaceFixDialog.tsx:94", now: "components/workspaces/WorkspaceFixDialog.tsx:94" },
    { frozen: "components/workspaces/WorkspaceGatePresetsPanel.tsx:201", now: "components/workspaces/WorkspaceGatePresetsPanel.tsx:201" },
  ];

  /** The sites this file could not decide at `c7f90df13`, and why. */
  const UNMEASURABLE_AT_FREEZE = [
    "components/chat/primitives/PrimitiveCard.tsx:179",
    "components/initiatives/plan/InitiativeNow.tsx:49",
    "components/views/primitives/chatPanePrimitive.tsx:125",
  ];

  /** The placeholder branches at `c7f90df13` — AC10(d)(4), AC8. */
  const PLACEHOLDERS_AT_FREEZE = [
    // Added by AC2's fix, deliberately and visibly: `.modal-body
    // usage-modal-body` held the loading line, the error line AND the loaded
    // report, so a tab stop on it would have announced "Usage by provider"
    // over the word "Loading…". AC8 required the three substitute states to
    // move into their own pane in its place, and that pane is a placeholder
    // branch by measurement — one line of text, no control, no collection.
    "components/UsageModal.tsx:190",
    "components/chat/DockChatWelcome.tsx:55",
    "pages/BranchTriagePage.tsx:242",
    "pages/InitiativePlanPage.tsx:247",
  ];

  const whereOf = (verdict: Verdict): string[] =>
    [...new Set(everySite.filter((entry) => entry.verdict === verdict).map(({ site }) => site.where))].sort();

  it("fixes every site the fix set was computed from, rather than relabelling it — mutant 9", () => {
    // The no-narrowing half, asserted on the IDENTITY of the set and not on
    // its residue. A fix measures `tabbable`, because a fixed element carries
    // `tabIndex={0}` and there is no separate term for one — so `tabbable` is
    // the ONLY verdict a frozen member may end on. `focusable-descendant`
    // reached by putting the tab stop on a descendant, or by moving a control
    // inside the region, is the narrowing (g1) cannot see.
    const byWhere = new Map(everySite.map(({ site, verdict }) => [site.where, verdict]));
    const notFixed = FIX_SET_AT_FREEZE.filter(({ now }) => byWhere.get(now) !== "tabbable").map(
      ({ frozen, now }) =>
        `${frozen} (now ${now}): ${byWhere.get(now) ?? "no longer a measured site"}, not tabbable`,
    );
    expect(notFixed).toEqual([]);
    // The frozen column is the identity, so its size is the claim: twenty-six
    // sites were measured unreachable before the fix and twenty-six rows must
    // still be standing here for the filter above to have asked about them.
    expect(new Set(FIX_SET_AT_FREEZE.map(({ frozen }) => frozen)).size).toBe(26);
  });

  it("lets no site reach the undecidable term that was not already there — mutant 11", () => {
    // AC10(h)'s own stated bound: "its count is asserted against the
    // measurement". It was not asserted anywhere — the only assertion over
    // this population was `toBeGreaterThanOrEqual(0)`, which every number
    // satisfies — so a spread attribute on a descendant moved a site out of
    // the fix set and into this term with nothing objecting. The term is the
    // one AC10(h) exists to keep from becoming a parking space, and an
    // unasserted count is exactly that parking space.
    expect(whereOf("unmeasurable")).toEqual([...UNMEASURABLE_AT_FREEZE].sort());
  });

  it("lets no site reach the placeholder term that was not already there — mutant 10", () => {
    // AC10(d)(4) requires a placeholder to be MEASURED, and the check beside
    // this one asks `isPlaceholder(site)` of a site classified by
    // `isPlaceholder` — true of anything, including a full output pane given
    // one `role="alert"`. AC8 says a fourth placeholder appearing is not a
    // contradiction, so this is shrink-only rather than a fixed figure: a new
    // one is added to this list deliberately and visibly, which is the whole
    // difference between a measurement and an allowlist.
    expect(whereOf("placeholder-site")).toEqual([...PLACEHOLDERS_AT_FREEZE].sort());
  });

});
