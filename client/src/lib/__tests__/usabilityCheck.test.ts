import { findUsabilityProblems } from "../usabilityCheck";

function kinds(html: string): string[] {
  const root = document.createElement("div");
  root.innerHTML = html;
  return findUsabilityProblems(root).map((p) => p.kind);
}

function repeat(count: number, item: (index: number) => string): string {
  return Array.from({ length: count }, (_, index) => item(index)).join("");
}

const record = (i: number) =>
  `<li><a href="/t/${i}">Ticket ${i}</a><span class="state">open</span><span class="agent">planner</span>` +
  `<span class="age">${i}d</span><span class="cost">$${i}</span></li>`;

describe("tabular-list", () => {
  it("flags list items that all repeat a name and four attributes", () => {
    expect(kinds(`<ul>${repeat(10, record)}</ul>`)).toContain("tabular-list");
  });

  it("passes a name with three short tags", () => {
    const item = (i: number) =>
      `<li><a href="/t/${i}">Ticket ${i}</a><span class="state">open</span>` +
      `<span class="agent">planner</span><span class="age">${i}d</span></li>`;
    expect(kinds(`<ul>${repeat(10, item)}</ul>`)).not.toContain("tabular-list");
  });

  it("does not count a control's labels or choices as fields", () => {
    const item = (i: number) =>
      `<li><a href="/t/${i}">Ticket ${i}</a><span class="state">open</span><button>Detach</button>` +
      `<select aria-label="Group"><option>A</option><option>B</option><option>C</option></select></li>`;
    expect(kinds(`<ul>${repeat(10, item)}</ul>`)).not.toContain("tabular-list");
  });

  it("passes a title with one detail line", () => {
    const item = (i: number) => `<li><a href="/t/${i}">Ticket ${i}</a><span>open · ${i}d</span></li>`;
    expect(kinds(`<ul>${repeat(10, item)}</ul>`)).not.toContain("tabular-list");
  });

  it("passes the same records when there are too few to compare", () => {
    expect(kinds(`<ul>${repeat(7, record)}</ul>`)).not.toContain("tabular-list");
  });

  it("passes items whose fields differ from one to the next", () => {
    const mixed = (i: number) =>
      i % 2 === 0 ? record(i) : `<li><a href="/n/${i}">Note ${i}</a><em>pinned</em><b>x</b><i>y</i><small>z</small></li>`;
    expect(kinds(`<ul>${repeat(10, mixed)}</ul>`)).not.toContain("tabular-list");
  });
});

describe("key-value-table", () => {
  const pair = (i: number) => `<tr><th scope="row">Field ${i}</th><td>value ${i}</td></tr>`;

  it("flags label/value rows with no column header", () => {
    expect(kinds(`<table><tbody>${repeat(4, pair)}</tbody></table>`)).toContain("key-value-table");
  });

  it("passes a two-column table with a header row", () => {
    const head = "<thead><tr><th>Name</th><th>Count</th></tr></thead>";
    expect(kinds(`<table>${head}<tbody>${repeat(4, pair)}</tbody></table>`)).not.toContain("key-value-table");
  });

  it("passes a table with more than two columns", () => {
    const row = (i: number) => `<tr><td>a${i}</td><td>b</td><td>c</td></tr>`;
    expect(kinds(`<table><tbody>${repeat(4, row)}</tbody></table>`)).not.toContain("key-value-table");
  });
});
