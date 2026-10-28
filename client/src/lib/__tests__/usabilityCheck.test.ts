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

  it("lets a board lane hold same-shaped cards, nested lists included", () => {
    const lane = `<div data-board-lane><ul>${repeat(10, record)}<li><ul>${repeat(10, record)}</ul></li></ul></div>`;
    expect(kinds(lane)).not.toContain("tabular-list");
  });

  it("lets a tree hold same-shaped rows, and still asks a long one for a filter", () => {
    const node = (i: number) =>
      `<div class="tree-node"><div role="treeitem" tabindex="0"><span class="prio">P3</span>` +
      `<span class="title">Ticket ${i}</span><span class="state">open</span>` +
      `<span class="agent">planner</span><span class="age">${i}d</span></div></div>`;
    expect(kinds(`<div role="tree">${repeat(10, node)}</div>`)).not.toContain("tabular-list");
    expect(kinds(`<div role="tree">${repeat(60, node)}</div>`)).toContain("unfiltered-long-list");
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

describe("rendered lists (cards, not <ul>)", () => {
  // The blindness this covers: /branch-triage rendered 224 `div.branch-triage-card`
  // and the checks, reading `ul, ol, tbody`, saw the 11-item nav sidebar.
  const card = (i: number) =>
    `<div class="branch-triage-card"><span class="name">branch ${i}</span><span class="age">${i}d</span></div>`;

  it("flags a long run of cards with nothing to click", () => {
    expect(kinds(`<div>${repeat(16, card)}</div>`)).toContain("dead-end-list");
  });

  it("flags a run of cards past the long-list threshold with no way to narrow it", () => {
    expect(kinds(`<div>${repeat(51, card)}</div>`)).toContain("unfiltered-long-list");
  });

  it("passes the same run when a filter sits near it", () => {
    const html = `<section><input type="search" aria-label="Filter" /><div>${repeat(51, card)}</div></section>`;
    expect(kinds(html)).not.toContain("unfiltered-long-list");
  });

  it("passes cards that each hold something to click", () => {
    const linked = (i: number) =>
      `<div class="branch-triage-card"><a href="/b/${i}">branch ${i}</a><span class="age">${i}d</span></div>`;
    expect(kinds(`<div>${repeat(16, linked)}</div>`)).not.toContain("dead-end-list");
  });

  it("groups a card with its selected variant, since the block class is the same", () => {
    const mixed = (i: number) =>
      i === 0
        ? `<div class="branch-triage-card is-selected"><span class="name">branch ${i}</span></div>`
        : card(i);
    expect(kinds(`<div>${repeat(16, mixed)}</div>`)).toContain("dead-end-list");
  });

  it("flags cards that repeat a name and four attributes", () => {
    const row = (i: number) =>
      `<div class="ticket-seg"><a href="/t/${i}">Ticket ${i}</a><span class="state">open</span>` +
      `<span class="agent">planner</span><span class="age">${i}d</span><span class="cost">$${i}</span></div>`;
    expect(kinds(`<div>${repeat(10, row)}</div>`)).toContain("tabular-list");
  });

  it("leaves an unclassed run alone, because that is layout and not a list", () => {
    const plain = (i: number) => `<div><span>row ${i}</span><span>${i}d</span></div>`;
    expect(kinds(`<div>${repeat(20, plain)}</div>`)).not.toContain("dead-end-list");
  });

  it("leaves a run of empty elements alone", () => {
    const blank = () => `<div class="spark-bar"></div>`;
    expect(kinds(`<div>${repeat(20, blank)}</div>`)).not.toContain("dead-end-list");
  });

  it("lets a board lane hold same-shaped cards", () => {
    const row = (i: number) =>
      `<div class="ticket-seg"><a href="/t/${i}">Ticket ${i}</a><span class="state">open</span>` +
      `<span class="agent">planner</span><span class="age">${i}d</span><span class="cost">$${i}</span></div>`;
    expect(kinds(`<div data-board-lane><div>${repeat(10, row)}</div></div>`)).not.toContain("tabular-list");
  });

  it("never asks a table to become a table", () => {
    const tr = (i: number) =>
      `<tr><td><a href="/t/${i}">Ticket ${i}</a></td><td>open</td><td>planner</td><td>${i}d</td><td>$${i}</td></tr>`;
    expect(kinds(`<table><tbody>${repeat(10, tr)}</tbody></table>`)).not.toContain("tabular-list");
  });

  it("does not read a graph's edges as a list of items with nothing to click", () => {
    // The memory map draws 50 `<line class="mm-inferred">` siblings. They repeat
    // one shape and none is clickable, but they are edges, not items.
    const edges = repeat(50, (i) => `<line class="mm-inferred" x1="${i}" y1="0" x2="0" y2="${i}">e${i}</line>`);
    expect(kinds(`<svg><g>${edges}</g></svg>`)).not.toContain("dead-end-list");
  });

  it("still flags a long card run that merely sits beside a drawing", () => {
    const html = `<div><svg><g><line class="e">x</line></g></svg><div>${repeat(16, card)}</div></div>`;
    expect(kinds(html)).toContain("dead-end-list");
  });

  it("counts a semantic list once, not again as a run of siblings", () => {
    const found = (() => {
      const root = document.createElement("div");
      root.innerHTML = `<ul>${repeat(16, (i) => `<li class="row"><span>row ${i}</span></li>`)}</ul>`;
      return findUsabilityProblems(root).filter((p) => p.kind === "dead-end-list");
    })();
    expect(found).toHaveLength(1);
  });
});

describe("nested-boxes", () => {
  // jsdom lays nothing out and reads no stylesheet. Give every element a
  // drawable size, and style the boxes inline, as a browser would compute them.
  const BOX = "border-top-left-radius: 8px; border-top: 1px solid red; border-right: 1px solid red; border-bottom: 1px solid red; border-left: 1px solid red;";
  const FILL = "border-top-left-radius: 8px; background-color: rgb(30, 40, 50);";

  function nested(html: string) {
    const root = document.createElement("div");
    root.innerHTML = html;
    document.body.appendChild(root);
    try {
      return findUsabilityProblems(root).filter((p) => p.kind === "nested-boxes");
    } finally {
      root.remove();
    }
  }

  beforeEach(() => {
    jest.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 400, height: 80 } as DOMRect);
  });
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("flags a box inside a box inside a box, naming the inner ones", () => {
    const html = `<section style="${BOX}"><div class="lane" style="${BOX}">${repeat(3, () => `<div class="stopped" style="${FILL}">blocked</div>`)}</div></section>`;
    const found = nested(html);
    expect(found).toHaveLength(1);
    expect(found[0].detail).toContain("3 boxes");
    expect(found[0].detail).toContain(".stopped ×3");
  });

  it("passes one card inside a panel: the region and the things in it", () => {
    expect(nested(`<section style="${BOX}">${repeat(4, () => `<div class="card" style="${BOX}">x</div>`)}</section>`)).toEqual([]);
  });

  it("passes rows split by rules inside a card", () => {
    const row = `<div class="row" style="border-top: 1px solid red;">x</div>`;
    expect(nested(`<section style="${BOX}"><div style="${BOX}">${repeat(3, () => row)}</div></section>`)).toEqual([]);
  });

  it("does not count controls or square blocks as boxes", () => {
    const control = `<button style="${BOX}">go</button><input style="${BOX}" />`;
    const square = `<div style="border-top: 1px solid red; border-right: 1px solid red; border-bottom: 1px solid red; border-left: 1px solid red;">sq</div>`;
    expect(nested(`<section style="${BOX}"><div style="${BOX}">${control}${square}</div></section>`)).toEqual([]);
  });

  it("stands aside where nothing is laid out, rather than calling everything flat", () => {
    jest.restoreAllMocks();
    const html = `<section style="${BOX}"><div style="${BOX}"><div style="${BOX}">x</div></div></section>`;
    expect(nested(html)).toEqual([]);
  });
});
