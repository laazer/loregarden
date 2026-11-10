/**
 * Every region this ticket fixes has the three per-site tests its criteria ask
 * for, and the list of which regions those are is COMPUTED rather than written
 * down anywhere.
 *
 * ## What this file is for
 *
 * AC3, AC8 and AC9 each say "one per fixed element site":
 *
 * - **AC3** — a keyboard test that finds the region by its EXPOSED role and
 *   its exact accessible name, and asserts the step to the control that
 *   actually serves it, in whichever direction that control sits. "A test
 *   asserting a forward Tab stop that does not exist does not satisfy this
 *   criterion, and neither does one asserting only that the element carries
 *   `tabIndex`."
 * - **AC8** — with zero rows, `queryByRole(<role>, { name })` is null and the
 *   substitute line is present. A tab stop that announces a name with nothing
 *   behind it is a dead stop and is indistinguishable from the failure state.
 * - **AC9** — for a region inside a `ModalShell`, Escape from inside it still
 *   closes the modal, because a region that consumed Escape would break
 *   `useDialogDismiss` for the whole dialog.
 *
 * Nothing enforced any of that per site, and "one per site" is exactly the
 * shape that goes missing quietly: the suite stays green while two thirds of
 * the sites have no test, because an absent test fails nothing. Three rounds
 * of this ticket each tried to hold the work list in prose and each list
 * disagreed with the tree, so this file holds none. It derives the sites from
 * the committed baseline and reports, per site, which of the three is missing.
 *
 * ## How it decides what a site needs
 *
 * The baseline's `tabbable` reason is what a fix measures as (AC10(d)(1)), so
 * every `tabbable` element site in `scrollRegionsAreReachable.test.ts` is a
 * region whose keyboard path this ticket is responsible for — the three that
 * already carried `tabIndex={0}` before it included, since AC3 names two of
 * them by file. AC9 applies only where the region renders inside a modal,
 * which is read from the component's own source rather than from a list.
 *
 * The test file a site's tests belong in is AC3's rule, not this file's
 * preference: the file that already covers that component, or a new file named
 * for it. `do not widen an unrelated suite`.
 *
 * ## What it can and cannot check
 *
 * It checks that the assertions EXIST, by shape, in the right file. It cannot
 * check that an accessible name is the right noun phrase or that a keyboard
 * step is the one a person would take — that is what writing the test is for,
 * and `ts.Program`-level cleverness would not add it. The precedent is this
 * repo's own fix-coverage gate, which finds a `prevented_by` test by its
 * literal name and no more.
 *
 * A file that satisfies this one with three assertions about the wrong element
 * is possible and is not the failure mode worth guarding: the failure mode is
 * twenty-six sites and nine tests, which is what the tree holds today.
 */

import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const SRC = path.resolve(__dirname, "..", "..");
const CENSUS = path.join(SRC, "test/scrollRegionsBaseline.ts");

// ---------------------------------------------------------------------------
// The sites, read out of the committed baseline with TypeScript's parser.
//
// Read rather than imported, for the reason the detector-tracking sibling
// states: importing would make this file agree with the census by construction
// about the one thing worth checking independently, and a baseline edited to
// quiet this file then has to be edited visibly.
// ---------------------------------------------------------------------------

interface Entry {
  selector: string;
  reason: string;
  sites: { where: string; reason: string }[];
}

function readBaseline(): Entry[] {
  const text = fs.readFileSync(CENSUS, "utf8");
  const source = ts.createSourceFile(CENSUS, text, ts.ScriptTarget.Latest, true);
  const out: Entry[] = [];
  const literal = (node: ts.Node | undefined): string =>
    node && ts.isStringLiteralLike(node) ? node.text : "";
  const field = (object: ts.ObjectLiteralExpression, name: string): ts.Node | undefined =>
    object.properties.find(
      (property): property is ts.PropertyAssignment =>
        ts.isPropertyAssignment(property) && property.name.getText(source) === name,
    )?.initializer;
  const walkNode = (node: ts.Node): void => {
    if (
      ts.isVariableDeclaration(node) &&
      node.name.getText(source) === "BASELINE" &&
      node.initializer &&
      ts.isArrayLiteralExpression(node.initializer)
    ) {
      for (const element of node.initializer.elements) {
        if (!ts.isObjectLiteralExpression(element)) continue;
        const sitesNode = field(element, "sites");
        const sites: Entry["sites"] = [];
        if (sitesNode && ts.isArrayLiteralExpression(sitesNode))
          for (const site of sitesNode.elements)
            if (ts.isObjectLiteralExpression(site))
              sites.push({ where: literal(field(site, "where")), reason: literal(field(site, "reason")) });
        out.push({
          selector: literal(field(element, "selector")),
          reason: literal(field(element, "reason")),
          sites,
        });
      }
    }
    ts.forEachChild(node, walkNode);
  };
  walkNode(source);
  return out;
}

/** One element site this ticket is responsible for the keyboard path of. */
interface FixedSite {
  /** `components/Foo.tsx:91`, exactly as the baseline writes it. */
  where: string;
  selector: string;
  /** Component source path relative to `client/src/`. */
  file: string;
  /** Renders inside a `ModalShell`, so AC9's Escape assertion applies. */
  inModal: boolean;
  /** The test file AC3 puts its tests in: the existing one, or the new one. */
  testFile: string;
  /** True when that file is the one that already covered this component. */
  testFileExists: boolean;
}

/**
 * The file AC3 names outright, which the heuristic below would get wrong.
 *
 * `LocalInstanceRow` has no suite of its own and AC3 says so explicitly —
 * "there is no `LocalInstanceRow.test.tsx`; do not create one" — because the
 * row only renders inside the modal that lists the instances, so a suite for
 * the row alone would have to mount that modal anyway. A rule that derives the
 * file from the component name cannot know that; the criterion does.
 */
const AC3_NAMES_THE_FILE: Record<string, string> = {
  "components/LocalInstanceRow.tsx": "components/__tests__/LocalInstancesModal.test.tsx",
};

/**
 * The test file AC3 names for a component: the file that already covers it, or
 * a new file named for it in the same `__tests__` directory.
 *
 * "Where a component has no test file today the test goes in a new file named
 * for that component; do not widen an unrelated suite." A component whose
 * suite is split over several files (`ImportTicketsModal.test.tsx` beside four
 * `.adversarial`/`.mutation` siblings) is covered by the plainest of them.
 */
function testFileFor(componentFile: string): { testFile: string; exists: boolean } {
  const named = AC3_NAMES_THE_FILE[componentFile];
  if (named !== undefined) return { testFile: named, exists: fs.existsSync(path.join(SRC, named)) };
  const directory = path.join(SRC, path.dirname(componentFile), "__tests__");
  const name = path.basename(componentFile, ".tsx");
  const plain = path.join(directory, `${name}.test.tsx`);
  if (fs.existsSync(plain)) return { testFile: path.relative(SRC, plain), exists: true };
  const siblings = fs.existsSync(directory)
    ? fs
        .readdirSync(directory)
        .filter((entry) => entry.startsWith(`${name}.`) && entry.endsWith(".test.tsx"))
        .sort()
    : [];
  const first = siblings[0];
  if (first !== undefined)
    return { testFile: path.relative(SRC, path.join(directory, first)), exists: true };
  return { testFile: path.relative(SRC, plain), exists: false };
}

const BASELINE = readBaseline();

/**
 * Every element site the baseline records as `tabbable`.
 *
 * A block whose every site agrees carries one reason and no site list, so
 * those sites are recovered from the tree; a block whose sites disagree names
 * each one, so those are read straight off. Either way no list of regions is
 * written down here, which is the property three rounds of this ticket lost.
 */
const fixedSites: FixedSite[] = (() => {
  const wheres: { entry: Entry; where: string }[] = [];
  for (const entry of BASELINE) {
    if (entry.reason === "tabbable")
      for (const where of sitesNaming(entry.selector)) wheres.push({ entry, where });
    for (const site of entry.sites)
      if (site.reason === "tabbable") wheres.push({ entry, where: site.where });
  }
  const byWhere = new Map<string, FixedSite>();
  for (const { entry, where } of wheres) {
    if (byWhere.has(where)) continue;
    const file = where.split(":")[0] ?? "";
    const { testFile, exists } = testFileFor(file);
    byWhere.set(where, {
      where,
      selector: entry.selector,
      file,
      inModal: rendersInModal(file),
      testFile,
      testFileExists: exists,
    });
  }
  return [...byWhere.values()].sort((a, b) => a.where.localeCompare(b.where));
})();

/**
 * Element sites naming a block-level `tabbable` selector.
 *
 * A block whose every site agrees carries one reason and no site list, so the
 * `where` has to be recovered from the tree. Only the subject's classes are
 * matched, per AC10(a)(i).
 */
function sitesNaming(selector: string): string[] {
  const tokens = selector
    .split(",")
    .flatMap((part) => {
      const subject = part.trim().split(/[\s>+~]+/).filter(Boolean).pop() ?? "";
      return [...subject.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)].map(([, name]) => name ?? "");
    })
    .filter(Boolean);
  if (tokens.length === 0) return [];
  const found: string[] = [];
  for (const file of walkTsx(SRC)) {
    const text = fs.readFileSync(file, "utf8");
    if (!tokens.some((token) => text.includes(token))) continue;
    const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true);
    const rel = path.relative(SRC, file);
    const visit = (node: ts.Node): void => {
      const open = ts.isJsxElement(node)
        ? node.openingElement
        : ts.isJsxSelfClosingElement(node)
          ? node
          : undefined;
      if (open) {
        const className = open.attributes.properties.find(
          (property): property is ts.JsxAttribute =>
            ts.isJsxAttribute(property) && property.name.getText(source) === "className",
        );
        if (className?.initializer) {
          const literals: string[] = [];
          const collect = (child: ts.Node): void => {
            if (ts.isStringLiteralLike(child)) literals.push(child.text);
            if (ts.isTemplateExpression(child))
              literals.push(child.head.text, ...child.templateSpans.map((span) => span.literal.text));
            ts.forEachChild(child, collect);
          };
          collect(className.initializer);
          const classes = literals.flatMap((value) => value.split(/\s+/)).filter(Boolean);
          if (tokens.some((token) => classes.includes(token))) {
            const { line } = source.getLineAndCharacterOfPosition(open.getStart(source));
            found.push(`${rel}:${line + 1}`);
          }
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }
  return found;
}

function walkTsx(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "node_modules" && entry.name !== "__tests__") walkTsx(full, out);
    } else if (full.endsWith(".tsx") && !/\.test\./.test(full)) out.push(full);
  }
  return out;
}

/**
 * Does this component render inside a dialog?
 *
 * AC9's Escape assertion applies to a region inside a `ModalShell`, because
 * `useDialogDismiss` closes the whole dialog on Escape and a region that
 * consumed it would break that. Read from the component's own source: it
 * renders a `ModalShell` itself, or it is a `.modal-body`/`.modal-footer`
 * component, which exists only inside one.
 */
function rendersInModal(componentFile: string): boolean {
  const full = path.join(SRC, componentFile);
  if (!fs.existsSync(full)) return false;
  const text = fs.readFileSync(full, "utf8");
  return /ModalShell|useDialogDismiss|modal-body|modal-footer/.test(text);
}

// ---------------------------------------------------------------------------
// The three assertion shapes, by what the criteria say they must contain.
// ---------------------------------------------------------------------------

/** AC3: an exposed role AND an exact accessible name, plus a keyboard step. */
const AC3 = {
  criterion: "AC3",
  what: "a getByRole(<role>, { name }) query and a Tab / Shift+Tab step to a named control",
  holds: (source: string): boolean =>
    /getByRole\(\s*["'`][a-z]+["'`]\s*,\s*\{[^}]*\bname\b/.test(source) &&
    /\.tab\(|userEvent\.tab|key:\s*["'`]Tab|\{Shift>\}|keyDown\([^)]*Tab/.test(source),
};

/** AC8: zero rows, the labelled box gone, the substitute line in its place. */
const AC8 = {
  criterion: "AC8",
  what: "a queryByRole(<role>, { name }) that is null at zero rows, beside the substitute line",
  holds: (source: string): boolean =>
    /queryByRole\(\s*["'`][a-z]+["'`]\s*,\s*\{[^}]*\bname\b/.test(source) &&
    /toBeNull\(\)|not\.toBeInTheDocument\(\)/.test(source),
};

/** AC9: Escape from inside the region still closes the dialog. */
const AC9 = {
  criterion: "AC9",
  what: "an Escape keydown from inside the region, asserting the modal still closes",
  holds: (source: string): boolean => /["'`]Escape["'`]|\{Escape\}/.test(source),
};

const read = (relative: string): string => {
  const full = path.join(SRC, relative);
  return fs.existsSync(full) ? fs.readFileSync(full, "utf8") : "";
};

// ---------------------------------------------------------------------------

describe("every fixed scroll region has its own keyboard, empty-state and Escape test", () => {
  it("derives the sites from the baseline, so nothing below vouches for an empty list", () => {
    // The guard on the guard. A parse that silently returned nothing would make
    // every assertion below pass while asking for no tests at all — which is
    // the exact failure this file exists to make impossible.
    expect(BASELINE.length).toBeGreaterThan(40);
    expect(fixedSites.length).toBeGreaterThan(0);
    // Every site resolves to a component file that exists, or the `where` the
    // baseline recorded has drifted and the sites below are fiction.
    expect(
      fixedSites.filter((site) => !fs.existsSync(path.join(SRC, site.file))).map((site) => site.where),
    ).toEqual([]);
  });

  it("puts each site's tests in the file AC3 names for its component", () => {
    // AC3's placement rule, reported rather than enforced into existence: a
    // component with no suite today gets a NEW file named for it, and no
    // unrelated suite is widened to hold it. This lists which files those are,
    // so the work is visible before it is missing.
    const needNewFile = fixedSites
      .filter((site) => !site.testFileExists)
      .map((site) => `${site.where} (${site.selector}) -> new file ${site.testFile}`);
    // Not an assertion about the count: it is reported, and the assertions
    // below are what fail. A site whose component already has a suite must use
    // it rather than a new file beside it.
    expect(needNewFile.length).toBeGreaterThanOrEqual(0);
    expect(
      fixedSites
        .filter((site) => site.testFileExists)
        .filter((site) => AC3_NAMES_THE_FILE[site.file] === undefined)
        .filter((site) => !site.testFile.includes(path.basename(site.file, ".tsx")))
        .map((site) => `${site.where} -> ${site.testFile} does not name its component`),
    ).toEqual([]);
  });

  it("holds every fixed site to an AC3 keyboard test in that file", () => {
    const missing = fixedSites
      .filter((site) => !AC3.holds(read(site.testFile)))
      .map((site) => `${site.where} (${site.selector}): ${site.testFile} lacks ${AC3.what}`);
    expect(missing).toEqual([]);
  });

  it("holds every fixed site to an AC8 empty-state assertion in that file", () => {
    const missing = fixedSites
      .filter((site) => !AC8.holds(read(site.testFile)))
      .map((site) => `${site.where} (${site.selector}): ${site.testFile} lacks ${AC8.what}`);
    expect(missing).toEqual([]);
  });

  it("holds every fixed site inside a modal to an AC9 Escape assertion in that file", () => {
    // Scoped by what the component renders, not by a list, so a site the
    // instrument adds brings its own Escape assertion with it rather than
    // being exempt for not having been named here.
    const missing = fixedSites
      .filter((site) => site.inModal)
      .filter((site) => !AC9.holds(read(site.testFile)))
      .map((site) => `${site.where} (${site.selector}): ${site.testFile} lacks ${AC9.what}`);
    expect(missing).toEqual([]);
  });
});
