/**
 * The ways lg-milestone-that-2215 can be made to pass while shipping the
 * defect, closed one at a time.
 *
 * `scrollRegionsAreReachable.test.ts` is the census: it enumerates every
 * bounded scrolling region and states the measured reason a keyboard reaches
 * it. This file is its adversary. Everything here was arrived at by asking one
 * question of the census and of the three component suites beside it — *what
 * could an implementer write that satisfies every assertion already committed
 * and still leaves the region unreachable, the bound moved, or the ring
 * drawn where the ticket says it must not be?* — and then writing the
 * assertion that answers it.
 *
 * Eight such mutants survived the committed suite. In rough order of how
 * plausible each is as an honest mistake rather than a contrivance:
 *
 * 1. **`tabIndex={-1}`.** The census records an element as tabbable when the
 *    `tabIndex` attribute is merely PRESENT, so `-1` — which is not a tab stop
 *    at all, and is the value a developer reaches for when they want a
 *    programmatic focus target — satisfied its check for a fixed region. (Fixed in
 *    the census too, in the same change; this file keeps the tree-level guard
 *    because the census's flag is used by three different checks.)
 * 2. **A second rule block for the same selector.** The census keys its
 *    measurements by selector text in a `Map`, so a second
 *    `.tf-lines { max-height: 90vh }` block anywhere in the tree collapses into
 *    the first and is invisible to it — while in the browser it is the one that
 *    wins, and the bound AC6 measured is gone.
 * 3. **Raising the bound in place.** `max-height: 240px` → `90vh` keeps the
 *    block a candidate, so shrink-only does not fire, and no committed
 *    assertion names the value for `.tf-lines` or `.local-instances-log`. AC2
 *    says the bound stays EXACTLY as it is; only `.wm-error-reason`'s was
 *    pinned.
 * 4. **Keeping the old per-stylesheet ring.** AC2 deletes the ring from
 *    `WorkflowMonitorView.css` and `TicketFailurePanel.css`. Only the first
 *    deletion is asserted anywhere; `.tf-lines:focus-visible` could stay, and
 *    then the one-definition property AC2 exists for is not true.
 * 5. **Ringing on `:focus`, not `:focus-visible`.** AC9 says selecting the
 *    text with a mouse must draw nothing. A `.scroll-focus-ring:focus` rule, or
 *    an unqualified `.scroll-focus-ring { outline }`, passes the census (which
 *    only looks AT the `:focus-visible` block) and draws on every click.
 * 6. **Widening the fix set.** AC2 fixes three regions and forbids widening;
 *    the census asserts the three BASELINE *reasons*, which is a claim about
 *    the baseline file. Sprinkling the class onto a fourth element in the tree
 *    changes no reason and fails nothing — and `.app-sidebar-list` is the one
 *    AC5 forbids by name, ahead of the app's primary navigation.
 * 7. **`role="group"` on a list.** AC2 forbids it on an element that already
 *    has a native role, because `group` overrides `list` and costs the
 *    "list, N items" announcement. Asserted for `.tf-lines` by its own suite;
 *    not for whatever else wears the class.
 * 8. **Animating the box.** The census rejects `line-clamp` and a transition
 *    naming `height`, which `transition: all` and `animation:` both slip past.
 *
 * Method is the census's own, and `classNamesResolve.test.ts`'s before it: read
 * the tree from disk, parse TSX with TypeScript's parser rather than a regex
 * over the source. The scan here is deliberately a SECOND, independent
 * instrument — it shares no code with the census, so a defect in one extractor
 * cannot hide in both. It asserts only convention-independent properties
 * (uniqueness, exact declarations, which elements wear a class), never a
 * denominator, so the two instruments cannot drift into disagreeing about a
 * count the way the retired 19/39/84/87 did.
 */

import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const SRC = path.resolve(__dirname, "../..");

/** The shared class AC2 defines once in `index.css`. */
const RING = "scroll-focus-ring";

/**
 * The two per-stylesheet rings AC2 names for DELETION, by file and line.
 *
 * This is not the fix set and may not be read as one. Round 4 withdrew the
 * enumeration of the fix set outright — the set is computed by
 * `scrollRegionsMeasuredReachability.test.ts`'s AC10(g1) and no criterion
 * states a figure for it (AC10(c)). What AC2 still names outright is two
 * deletions and the three regions whose BOUNDS are pinned below, because those
 * are statements about specific stylesheet lines rather than about the
 * population.
 */
const RING_DELETIONS = [".wm-error-reason", ".tf-lines"] as const;

/**
 * The declarations AC2/AC6/AC7 say stay exactly as they are.
 *
 * Each region's bound is the defect this ticket describes and the thing it must
 * not touch, and for two of the three the figures are load-bearing elsewhere:
 * AC6's "roughly 87% of the worst real case is reachable only by scrolling"
 * rests on 240px at `--fs-small`/1.6, and AC7's "about 15 lines" on 240px at
 * `--fs-micro` with `pre-wrap`.
 */
const PINNED: Record<string, Record<string, string>> = {
  ".tf-lines": {
    "max-height": "240px",
    overflow: "auto",
    "font-size": "var(--fs-small)",
    "line-height": "1.6",
  },
  ".local-instances-log": {
    "max-height": "240px",
    overflow: "auto",
    "font-size": "var(--fs-micro)",
    "white-space": "pre-wrap",
  },
  ".wm-error-reason": { "max-height": "9em", overflow: "auto" },
};

// ---------------------------------------------------------------------------
// A second extractor, sharing nothing with the census's.
// ---------------------------------------------------------------------------

function tree(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "node_modules") tree(full, out);
    } else out.push(full);
  }
  return out;
}

const FILES = tree(SRC);
const CSS = FILES.filter((file) => file.endsWith(".css"));
const TSX = FILES.filter((file) => file.endsWith(".tsx") && !/__tests__|\.test\./.test(file));

interface Block {
  selector: string;
  declarations: Record<string, string>;
  body: string;
  file: string;
}

/** Every innermost rule block in the tree, comments stripped first. */
const blocks: Block[] = CSS.flatMap((file) => {
  const source = fs.readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  return Array.from(source.matchAll(/([^{}]+)\{([^{}]*)\}/g), ([, rawSelector, body]) => {
    const declarations: Record<string, string> = {};
    for (const statement of body.split(";")) {
      const at = statement.indexOf(":");
      if (at < 0) continue;
      declarations[statement.slice(0, at).trim().toLowerCase()] = statement.slice(at + 1).trim();
    }
    return {
      selector: rawSelector.trim().replace(/\s+/g, " "),
      declarations,
      body,
      file: path.relative(SRC, file),
    };
  }).filter((block) => block.selector !== "" && !block.selector.startsWith("@"));
});

/**
 * A rule block that clips: a scrolling overflow, plus something bounding it on
 * an axis it actually scrolls.
 *
 * The axis term is the whole of AC-DEPS's finding about this file. It was
 * written to share no code with the census so that a defect in one could not
 * hide in both — and it missed the same class of region anyway, because BOTH
 * implemented AC10(b) and every term AC10(b) then named was vertical. Two
 * independent instruments cannot disagree about a class of region neither one
 * can see. The independence is still worth having; what it cannot buy is a
 * candidate definition.
 */
const isCandidate = (block: Block): boolean => clipsOn(block).length > 0;

/** Every axis this block both scrolls and is bounded on. */
const clipsOn = (block: Block): ("x" | "y")[] => {
  const axes: ("x" | "y")[] = [];
  for (const axis of ["x", "y"] as const) {
    const own = block.declarations[`overflow-${axis}`];
    const words = (block.declarations["overflow"] ?? "").split(/\s+/).filter(Boolean);
    const scrolls =
      own !== undefined && own !== ""
        ? /^(auto|scroll)$/.test(own)
        : words.length > 0 && /^(auto|scroll)$/.test((axis === "x" ? words[0] : (words[1] ?? words[0])) ?? "");
    if (!scrolls) continue;
    const extent = axis === "x" ? "width" : "height";
    const declared = block.declarations[`max-${extent}`] ?? block.declarations[extent];
    if (declared !== undefined && !/^(none|auto)$/.test(declared)) {
      axes.push(axis);
      continue;
    }
    // `min-<extent>: 0` exactly, not `0.5rem`: a flex child told it may be
    // smaller than its content is the flex-bound kind, and a 0.5rem floor is
    // not.
    if (/^0(?:[a-z%]+)?$/i.test(block.declarations[`min-${extent}`] ?? "")) {
      axes.push(axis);
      continue;
    }
    const grow = block.declarations["flex-grow"] ?? block.declarations["flex"]?.split(/\s+/)[0];
    if (grow !== undefined && Number.parseFloat(grow) > 0) axes.push(axis);
  }
  return axes;
};

/** One JSX element that names a class statically, and what it declares. */
interface Site {
  where: string;
  tag: string;
  classes: string[];
  /** Source text of `tabIndex`'s value, or undefined when absent. */
  tabIndex?: string;
  /** Source text of `role`'s value, quotes stripped, or undefined. */
  role?: string;
  /**
   * Does its JSX subtree hold something that takes focus on its own?
   *
   * Read syntactically, which is this file's whole method: it shares no code
   * with the census and so answers the question a second way or not at all.
   * Coarser than the census's `ts.Program` walk — it cannot follow a component
   * tag to its declaration — and deliberately so, because it is used here only
   * to EXCUSE a site from the ring, and the conservative direction for an
   * excuse is to grant it rarely.
   */
  focusableDescendant: boolean;
  /**
   * Does its subtree hold markup this scan cannot see into?
   *
   * An expression child that is a call or a bare identifier renders markup
   * declared elsewhere, and only a type checker can follow it — which is the
   * census's job and explicitly not this file's. `.plan-gantt-scroll` is the
   * case that forced the distinction: the census resolves four <Button>s in
   * its chart, reached through `{rows.map(renderRow)}`, and this scan sees a
   * call and nothing else. Two instruments CAN disagree about a subtree one of
   * them cannot see, so a site in this state is excused rather than reported
   * on a reading that is known to be the weaker of the two.
   */
  opaqueSubtree: boolean;
}

/** Tags that take focus with no `tabIndex` of their own. */
const INTERACTIVE = new Set([
  "a",
  "button",
  "input",
  "select",
  "textarea",
  "summary",
  "Button",
  "IconCloseButton",
  "Input",
  "Select",
  "Textarea",
  "Link",
  "NavLink",
]);

const sites: Site[] = TSX.flatMap((file) => {
  const text = fs.readFileSync(file, "utf8");
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const rel = path.relative(SRC, file);
  const out: Site[] = [];
  const walk = (node: ts.Node): void => {
    if (ts.isJsxOpeningLikeElement(node)) {
      const attribute = (name: string): ts.JsxAttribute | undefined =>
        node.attributes.properties.find(
          (property): property is ts.JsxAttribute =>
            ts.isJsxAttribute(property) && property.name.getText(source) === name,
        );
      const className = attribute("className");
      if (className?.initializer) {
        // Every string the class expression can contain, however it is built:
        // a literal, a template, a `cn()` call. Taking the literals inside the
        // initializer rather than its source text means a token is not found
        // merely because it appears in a neighbouring comment or variable name.
        const literals: string[] = [];
        const collect = (child: ts.Node): void => {
          if (ts.isStringLiteralLike(child)) literals.push(child.text);
          if (ts.isTemplateExpression(child)) {
            literals.push(child.head.text, ...child.templateSpans.map((span) => span.literal.text));
          }
          ts.forEachChild(child, collect);
        };
        collect(className.initializer);
        const classes = literals.flatMap((literal) => literal.split(/\s+/)).filter(Boolean);
        const value = (name: string): string | undefined => {
          const found = attribute(name)?.initializer;
          if (!found) return undefined;
          const expression = ts.isJsxExpression(found) ? found.expression : found;
          return expression ? expression.getText(source).replace(/^["'`]|["'`]$/g, "") : undefined;
        };
        const { line } = source.getLineAndCharacterOfPosition(node.getStart(source));
        // The enclosing element, so "subtree" means its children and not the
        // whole file from this point on. A self-closing tag has none.
        const element = ts.isJsxElement(node.parent) ? node.parent : undefined;
        let focusableDescendant = false;
        let opaqueSubtree = false;
        if (element)
          for (const child of element.children) {
            const scan = (inner: ts.Node): void => {
              if (ts.isJsxOpeningLikeElement(inner) && INTERACTIVE.has(inner.tagName.getText(source)))
                focusableDescendant = true;
              if (
                ts.isJsxExpression(inner) &&
                inner.expression &&
                (ts.isCallExpression(inner.expression) || ts.isIdentifier(inner.expression))
              )
                opaqueSubtree = true;
              ts.forEachChild(inner, scan);
            };
            scan(child);
          }
        out.push({
          where: `${rel}:${line + 1}`,
          tag: node.tagName.getText(source),
          classes,
          tabIndex: value("tabIndex"),
          role: value("role"),
          focusableDescendant,
          opaqueSubtree,
        });
      }
    }
    ts.forEachChild(node, walk);
  };
  walk(source);
  return out;
});

const wearing = (token: string): Site[] => sites.filter((site) => site.classes.includes(token));

/** Elements that are native lists or tables, where `group` overrides a role. */
const NATIVE_ROLE = new Set(["ol", "ul", "table", "nav", "main", "dl"]);

// ---------------------------------------------------------------------------

describe("the ways this ticket can pass while shipping the defect", () => {
  it("extracts a corpus from both sides, so nothing below vouches for an empty scan", () => {
    // The guard on this guard. An independent extractor that silently returned
    // nothing would make every assertion here pass while measuring the tree not
    // at all, which is the failure mode a second instrument exists to avoid
    // rather than to introduce.
    expect(CSS.length).toBeGreaterThan(80);
    expect(TSX.length).toBeGreaterThan(200);
    expect(blocks.filter(isCandidate).length).toBeGreaterThan(40);
    expect(sites.length).toBeGreaterThan(500);
  });

  it("wears the ring only with a real tab stop, at 0 and not -1", () => {
    // Mutants 1 and 6, which round 4 merged because the list that separated
    // them is gone. The census asserts things about baseline ENTRIES; this
    // asserts things about ELEMENTS in the tree, which is the claim an
    // operator meets.
    //
    // There is no list of three here any more. The set AC2 fixes is computed
    // by the sibling census and no criterion states a figure for it, so what
    // this file can check INDEPENDENTLY is the shape of every fix in the tree:
    // a literal non-negative `tabIndex` beside the ring, never `-1`, which is
    // not a tab stop at all and is the value a developer reaches for when they
    // want a programmatic focus target. Nothing about `-1` looks wrong in a
    // diff.
    const worn = wearing(RING);
    expect(worn.length).toBeGreaterThan(0);
    expect(
      worn
        .filter((site) => {
          const value = Number.parseFloat(site.tabIndex ?? "");
          return !Number.isFinite(value) || value < 0;
        })
        .map((site) => `${site.where}: <${site.tag}> tabIndex={${site.tabIndex ?? "absent"}}`),
    ).toEqual([]);
    // And the converse, which is the half a wearer-only check cannot see: a
    // bounded region made a tab stop WITHOUT the shared class draws no ring at
    // all, and a region whose focus is invisible is the defect with a tab stop
    // added to it.
    //
    // Scoped to the regions this ticket MADE tab stops, which is the fix set
    // and is read here as "nothing in the subtree can take focus". A region
    // that carried its own `tabIndex` AND holds focusable descendants —
    // `.plan-gantt-scroll`, whose chart holds four <Button>s — is
    // `focusable-descendant`, so AC5 says it gets no change and AC10(g2)
    // fails the ring on it outright. Demanding the ring there too would make
    // this assertion and that criterion contradict each other, with the site
    // unable to satisfy both.
    const candidateClasses = new Set(
      blocks
        .filter(isCandidate)
        .flatMap((block) =>
          block.selector
            .split(",")
            .map((part) => part.trim().split(/[\s>+~]+/).filter(Boolean).pop() ?? "")
            .flatMap((subject) => [...subject.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)].map(([, name]) => name)),
        ),
    );
    expect(
      sites
        .filter((site) => site.classes.some((token) => candidateClasses.has(token)))
        .filter((site) => {
          const value = Number.parseFloat(site.tabIndex ?? "");
          return Number.isFinite(value) && value >= 0;
        })
        .filter((site) => !site.classes.includes(RING))
        .filter((site) => !site.focusableDescendant && !site.opaqueSubtree)
        .map((site) => `${site.where}: <${site.tag}> is a tab stop with no .${RING}`),
    ).toEqual([]);
  });

  it("takes role=group only where the tag has no role of its own", () => {
    // Mutant 7. `group` overrides `list`, so an <ol> or <ul> that takes it
    // stops announcing "list, N items" — the thing AC6's query reads — and an
    // `aria-label` on a generic <div>/<pre>/<span> is not reliably exposed
    // without one. Both directions, over whatever wears the class, because
    // round 4 withdrew the list this used to be anchored on.
    const worn = wearing(RING);
    expect(worn.length).toBeGreaterThan(0);
    const wrong = worn
      .filter((site) =>
        NATIVE_ROLE.has(site.tag.toLowerCase()) ? site.role !== undefined : site.role !== "group",
      )
      .map(
        (site) =>
          `${site.where}: <${site.tag}> ${site.role ?? "no role"}` +
          (NATIVE_ROLE.has(site.tag.toLowerCase()) ? " (native role overridden)" : " (generic, needs group)"),
      );
    expect(wrong).toEqual([]);
  });

  it("leaves the four .app-sidebar-list lists without a tab stop of their own", () => {
    // Mutant 6 again, at the one place AC5 forbids by name. These are <ul>s of
    // links and buttons — already reachable, every row of them — and the
    // measured sidebar scrolls for real (2,348 tickets in the loregarden
    // workspace). A `tabIndex={0}` here inserts four dead stops AHEAD of the
    // app's primary navigation, and the detector's 24 pre-fix firings on this
    // selector were lg-milestone-that-2223, a height-filter no-op, not
    // instances.
    const lists = wearing("app-sidebar-list");
    expect(lists).toHaveLength(4);
    expect(lists.filter((site) => site.tabIndex !== undefined || site.classes.includes(RING))).toEqual([]);
  });

  it("deletes the per-stylesheet rings rather than leaving a second copy behind", () => {
    // Mutant 4. One definition is the entire point of the shared class: three
    // copies of one rule is how their offsets drift, which is the state AC2
    // found the tree in. `.wm-error-reason`'s deletion is asserted by its own
    // suite; `.tf-lines`'s was not asserted anywhere, and
    // `.local-instances-log` never had one to keep.
    const own = blocks
      .filter((block) => RING_DELETIONS.some((selector) => block.selector.includes(selector)))
      .filter((block) => /:focus(-visible)?\b/.test(block.selector))
      .map((block) => `${block.file}: ${block.selector}`);
    expect(own).toEqual([]);
  });

  it("draws the ring on :focus-visible and in no other state", () => {
    // Mutant 5. AC9: selecting the text with a mouse must draw nothing, which
    // is the whole reason this is `:focus-visible` and not `:focus`. The census
    // reads the `:focus-visible` block and is blind to a sibling rule, so
    // `.scroll-focus-ring:focus { outline: … }` beside it passes everything and
    // rings on every click.
    const mentions = blocks.filter((block) => block.selector.includes(RING));
    expect(mentions.map((block) => `${block.file}: ${block.selector}`)).toEqual([
      `index.css: .${RING}:focus-visible`,
    ]);
    // Inset, as both rules it replaces already chose: an outset ring on a
    // clipping box is drawn under its neighbours.
    expect(mentions[0]?.declarations).toEqual({
      outline: "2px solid var(--ac)",
      "outline-offset": "-2px",
    });
  });

  it("keeps each fixed region's bound in one block, with the figures its criteria rest on", () => {
    // Mutants 2 and 3, which are the same mutant written two ways. The census
    // keys its measurements by selector in a `Map`, so a SECOND
    // `.tf-lines { max-height: 90vh }` block collapses into the first and is
    // invisible to it — and in the browser the second one wins. Raising the
    // bound in place is the simpler version: the block is still a candidate, so
    // shrink-only never fires, and no committed assertion named the value for
    // two of the three.
    for (const [selector, declarations] of Object.entries(PINNED)) {
      const own = blocks.filter((block) => block.selector === selector);
      expect(own.map((block) => block.file)).toHaveLength(1);
      for (const [property, value] of Object.entries(declarations)) {
        expect(`${selector} ${property}: ${own[0]?.declarations[property]}`).toBe(
          `${selector} ${property}: ${value}`,
        );
      }
    }
  });

  it("animates no part of any bounded region's box, by any spelling", () => {
    // Mutant 8. The census rejects `line-clamp` and a transition naming
    // `height`, so `transition: all` and `animation: grow …` both pass it while
    // doing the forbidden thing — and `transition: all` is the likelier of the
    // two to be written by accident. Checked over EVERY candidate rather than
    // only the fixed ones, because a region this ticket merely RECORDS is
    // still a region whose bound it must not touch (AC10(h)).
    const offenders: string[] = [];
    for (const block of blocks.filter(isCandidate)) {
      const transition = `${block.declarations["transition"] ?? ""} ${block.declarations["transition-property"] ?? ""}`;
      if (/\b(all|height|max-height|min-height|flex|flex-basis)\b/.test(transition)) {
        offenders.push(`${block.file} ${block.selector}: transition ${transition.trim()}`);
      }
      if (block.declarations["animation"] !== undefined || block.declarations["animation-name"] !== undefined) {
        offenders.push(`${block.file} ${block.selector}: animation`);
      }
      if (/line-clamp/.test(block.body)) offenders.push(`${block.file} ${block.selector}: line-clamp`);
    }
    expect(offenders).toEqual([]);
  });

  it("names no candidate selector twice, which is what the census cannot see", () => {
    // The census's instrument, not the ticket's subject. Its `Map` is keyed by
    // selector text, so two candidate blocks with the same selector become one
    // measurement and one baseline entry — the later block's bound, overflow
    // and reason all unexamined. There are none today; this fails on the day
    // one is added, in the file whose own conventions depend on it.
    const seen = new Map<string, string[]>();
    for (const block of blocks.filter(isCandidate)) {
      const at = seen.get(block.selector);
      if (at) at.push(block.file);
      else seen.set(block.selector, [block.file]);
    }
    expect([...seen].filter(([, files]) => files.length > 1)).toEqual([]);
  });
});
