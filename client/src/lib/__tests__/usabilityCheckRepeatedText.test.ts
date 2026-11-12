import { readFileSync } from "node:fs";
import { join } from "node:path";

import { findUsabilityProblems } from "../usabilityCheck";

/**
 * lg-milestone-that-2599 — the fold and the exemption, with the measured
 * margin each one leaves the other. Both halves are load-bearing: the fold is
 * what catches the Monitor's template walls, and the exemption is what stops a
 * list keyed by an identifier from reading as one repeated sentence, which is
 * how `/branch-triage` was reported UNUSABLE over 46 distinguishable branches.
 */

function repeat(count: number, item: (index: number) => string): string {
  return Array.from({ length: count }, (_, index) => item(index)).join("");
}

function repeatedText(html: string) {
  const root = document.createElement("div");
  root.innerHTML = html;
  return findUsabilityProblems(root).filter((p) => p.kind === "repeated-text");
}

const SENTENCE = "Nothing here has happened in a while, check the queue.";
const stage = (n: number) => `Stage 'review' ran ${n} times in one orchestration run.`;
const FOLDED_STAGE = "Stage '…' ran # times in one orchestration run.";

describe("repeated-text folds a template", () => {
  it("flags a wall of one genuinely identical sentence", () => {
    expect(repeatedText(`<ul>${repeat(13, () => `<li>${SENTENCE}</li>`)}</ul>`)).toHaveLength(1);
  });

  it("flags a template wall whose numbers differ, because that is still one sentence", () => {
    // 18 rows over 8 distinct unfolded strings: the Monitor's own shape.
    const rows = repeat(18, (i) => `<li>${stage(i % 8)}</li>`);
    expect(repeatedText(`<ul>${rows}</ul>`).map((p) => p.detail)).toEqual([
      `18 elements say "${FOLDED_STAGE}" — group or fold them`,
    ]);
  });

  it("stays silent at the threshold, identical or not", () => {
    expect(repeatedText(`<ul>${repeat(12, () => `<li>${SENTENCE}</li>`)}</ul>`)).toEqual([]);
  });

  it("skips on the folded text, so a short template never enters the pass", () => {
    // Identical unfolded and 23 characters long, but "Run # finished" is under
    // the 20-character floor, so the pass never sees these rows at all.
    expect(repeatedText(`<ul>${repeat(20, () => "<li>Run 1234567890 finished</li>")}</ul>`)).toEqual([]);
  });
});

describe("repeated-text exempts rows the fold only made alike", () => {
  it("exempts a wall whose unfolded rows are every one of them distinct", () => {
    const rows = repeat(46, (i) => `<li>loregarden/lg-milestone-that-${2000 + i} diverged</li>`);
    expect(repeatedText(`<ul>${rows}</ul>`)).toEqual([]);
  });

  it("flags the same wall again as soon as one single pair of rows is identical", () => {
    // 13 rows, 12 distinct unfolded: the exemption is exact equality, so one
    // duplicate is enough, and the report still counts the folded group.
    const rows = repeat(13, (i) => `<li>loregarden/lg-milestone-that-${2000 + Math.min(i, 11)} diverged</li>`);
    expect(repeatedText(`<ul>${rows}</ul>`).map((p) => p.detail)).toEqual([
      '13 elements say "loregarden/lg-milestone-that-# diverged" — group or fold them',
    ]);
  });

  it("reports the folded group's own count, never a recount of its duplicates", () => {
    // 20 rows over 19 distinct: a recounted duplicate count would be 2, below
    // the threshold, and this group would vanish instead of being reported.
    const rows = repeat(20, (i) => `<li>${stage(Math.min(i, 18))}</li>`);
    expect(repeatedText(`<ul>${rows}</ul>`).map((p) => p.detail)).toEqual([
      `20 elements say "${FOLDED_STAGE}" — group or fold them`,
    ]);
  });

  it("still flags every Monitor bucket in the production-shaped findings", () => {
    const findings = JSON.parse(
      readFileSync(join(__dirname, "..", "..", "test", "fixtures", "prod-shape", "monitor-findings.json"), "utf8"),
    ) as { summary: string }[];
    const root = document.createElement("div");
    const list = document.createElement("ul");
    for (const finding of findings) {
      const item = document.createElement("li");
      item.textContent = finding.summary;
      list.appendChild(item);
    }
    root.appendChild(list);

    // The three measured buckets: 58 occurrences over 37 distinct unfolded
    // strings, 52 over 16, 18 over 8. Surpluses 21, 36 and 10, so the strict
    // exemption leaves all three standing with a margin of at least 10.
    expect(
      findUsabilityProblems(root)
        .filter((p) => p.kind === "repeated-text")
        .map((p) => p.detail)
        .sort(),
    ).toEqual(
      [
        `58 elements say "Stage '…' ran # times in one orchestration run (baseline # attempts per stage)." — group or fold them`,
        `52 elements say "Stage '…' has # successful duration samples (need #); configured floor is #s. No" — group or fold them`,
        `18 elements say "Run has been RUNNING for #h, past the #h bound for this stage." — group or fold them`,
      ].sort(),
    );
  });
});

describe("repeated-text keeps the fold itself", () => {
  const SOURCE = readFileSync(join(__dirname, "..", "usabilityCheck.ts"), "utf8");

  it("leaves the three normalizations and the 20-character skip byte-unchanged", () => {
    // The exemption changed WHICH groups are reported and must not have
    // changed which elements enter the pass: the Monitor's buckets exist only
    // because these three normalizations run before the comparison, and the
    // skip is measured on the folded text, not the raw one.
    for (const line of [
      '.replace(/\\s+/g, " ")',
      '.replace(/([\'"`])[^\'"`]{1,60}\\1/g, "\'…\'")',
      '.replace(/\\d+(\\.\\d+)?/g, "#")',
      "if (text.length < 20) continue;",
    ]) {
      expect(SOURCE).toContain(line);
    }
  });
});

/**
 * The two ways an exemption can be built from the wrong string. Both of these
 * pass every case above, so neither is hypothetical: one compares raw
 * `textContent` and reads a template's own indentation as distinctness, the
 * other undoes only the digit fold and leaves the quoted-name fold standing.
 */
describe("repeated-text measures the unfolded rows exactly", () => {
  const STAGES = [
    "triage",
    "plan",
    "spec",
    "design",
    "implement",
    "verify",
    "review",
    "gate",
    "land",
    "merge",
    "absorb",
    "adapt",
    "report",
  ];

  it("collapses whitespace first, so a template's indentation is not distinctness", () => {
    // Thirteen copies of ONE sentence, laid out the way a template lays them
    // out: every row's raw textContent differs, every one collapses to the
    // same sentence. Compared raw, this reads as 13 distinct strings and a
    // genuine wall goes silent.
    const rows = repeat(
      13,
      (i) =>
        `<li>\n${" ".repeat(i + 1)}Nothing here has happened\n${"\t".repeat(i + 1)}in a while, check the queue.\n</li>`,
    );
    expect(repeatedText(`<ul>${rows}</ul>`).map((p) => p.detail)).toEqual([
      `13 elements say "${SENTENCE}" — group or fold them`,
    ]);
  });

  it("undoes the quoted-name fold as well as the digit fold", () => {
    // Thirteen rows with no digit anywhere in them, distinguished only by the
    // quoted name the fold replaces with '…'. An exemption that undid only
    // `\d+` would read one sentence here and report a wall that never repeated.
    const rows = repeat(STAGES.length, (i) => `<li>Stage '${STAGES[i]}' is waiting on an approval.</li>`);
    expect(repeatedText(`<ul>${rows}</ul>`)).toEqual([]);
  });

  it("exempts one row above the threshold too, with no tolerance to spare", () => {
    // The pair with "flags the same wall again as soon as one single pair of
    // rows is identical" above: 13 over 13 is exempt, 13 over 12 is reported.
    // No ratio can separate two groups one duplicate apart.
    const rows = repeat(13, (i) => `<li>loregarden/lg-milestone-that-${2000 + i} diverged</li>`);
    expect(repeatedText(`<ul>${rows}</ul>`)).toEqual([]);
  });

  it("decides each folded group on its own rows, in either document order", () => {
    // One exempt wall and one genuine one on the same page. A set shared
    // across groups, or carried over from the previous group, reports or
    // silences the wrong one — and does it differently depending on which
    // group the walk reaches first.
    const wall = (i: number) => `<li>loregarden/lg-milestone-that-${2000 + i} diverged</li>`;
    const prose = () => `<li>${SENTENCE}</li>`;
    const expected = [`13 elements say "${SENTENCE}" — group or fold them`];
    expect(repeatedText(`<ul>${repeat(13, wall)}${repeat(13, prose)}</ul>`).map((p) => p.detail)).toEqual(expected);
    expect(repeatedText(`<ul>${repeat(13, (i) => wall(i) + prose())}</ul>`).map((p) => p.detail)).toEqual(expected);
  });
});
