/**
 * Find the shapes an unusable surface takes, in rendered DOM.
 *
 * Every one of these shipped past the five-state rules and the UX gate, which
 * see only shape: the Monitor printed 62 copies of "Stage '…' ran # times…" in
 * a list with no link in it; the Memory map drew 33 records and no edge. A
 * static gate cannot see them because they appear only at production volume —
 * so this runs where production-shaped data is rendered: jest over the recorded
 * `prod-shape` fixtures (in CI), and `npm run visual-qa` inside each page.
 *
 * It flags shapes, not taste. A hit is a question for a person ("should 94 rows
 * of this be here?"); a pass is not a verdict that the surface is good.
 *
 * Deliberately self-contained — one function, no imports, nothing from module
 * scope: `scripts/visual-qa.mjs` transpiles this file and evaluates the
 * function inside the browser page, where nothing else from the app exists.
 */

export type UsabilityProblemKind =
  | "repeated-text"
  | "dead-end-list"
  | "edgeless-graph"
  | "unfiltered-long-list";

export interface UsabilityProblem {
  kind: UsabilityProblemKind;
  detail: string;
}

export function findUsabilityProblems(root: ParentNode): UsabilityProblem[] {
  /** The same sentence this many times is a wall, whatever varies inside it. */
  const REPEATED = 12;
  /** A list this long with nothing to click is a dead end. */
  const DEAD_END_ITEMS = 15;
  /** A list this long needs a way to narrow it. */
  const LONG_LIST = 50;
  const ACTIONABLE =
    "a[href], button, input, select, textarea, summary, [role='button'], [role='link'], [tabindex]";
  const NARROWING = "input[type='search'], input[type='text'], select, [role='searchbox']";

  const problems: UsabilityProblem[] = [];

  // A template repeated with different numbers or quoted names is still one
  // sentence to the reader, so digits and quoted words are folded.
  const counts = new Map<string, number>();
  for (const el of Array.from(root.querySelectorAll("li, p, td, [role='listitem']"))) {
    const text = (el.textContent ?? "")
      .replace(/\s+/g, " ")
      .replace(/(['"`])[^'"`]{1,60}\1/g, "'…'")
      .replace(/\d+(\.\d+)?/g, "#")
      .trim();
    if (text.length < 20) continue;
    counts.set(text, (counts.get(text) ?? 0) + 1);
  }
  for (const [text, count] of counts) {
    if (count > REPEATED) {
      problems.push({
        kind: "repeated-text",
        detail: `${count} elements say "${text.slice(0, 80)}" — group or fold them`,
      });
    }
  }

  for (const list of Array.from(root.querySelectorAll("ul, ol, tbody"))) {
    const items = Array.from(list.children).filter((c) => c.tagName === "LI" || c.tagName === "TR");
    if (items.length > DEAD_END_ITEMS && !items.some((item) => item.matches(ACTIONABLE) || item.querySelector(ACTIONABLE))) {
      problems.push({
        kind: "dead-end-list",
        detail: `a list of ${items.length} items with nothing to click in any of them`,
      });
    }
    if (items.length > LONG_LIST) {
      let scope: Element | null = list;
      let narrowed = false;
      for (let depth = 0; scope && depth < 4 && !narrowed; depth++) {
        narrowed = scope.querySelector(NARROWING) !== null;
        scope = scope.parentElement;
      }
      if (!narrowed) {
        problems.push({
          kind: "unfiltered-long-list",
          detail: `a list of ${items.length} items with no search or filter near it`,
        });
      }
    }
  }

  for (const svg of Array.from(root.querySelectorAll("svg"))) {
    const nodes = svg.querySelectorAll("[role='button']").length;
    const edges = svg.querySelectorAll("line, path").length;
    if (nodes >= 5 && edges === 0) {
      problems.push({
        kind: "edgeless-graph",
        detail: `a graph of ${nodes} nodes with no edges — it shows nothing a list would not`,
      });
    }
  }

  return problems;
}
