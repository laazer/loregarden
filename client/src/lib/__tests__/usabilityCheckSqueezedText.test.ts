import { findUsabilityProblems } from "../usabilityCheck";

/** jsdom lays nothing out, so each element's size comes from its own `data-*`. */
describe("squeezed-text", () => {
  const TITLE = "An in-process runner, behaviour unchanged";

  function squeezed(html: string) {
    const root = document.createElement("div");
    root.innerHTML = html;
    document.body.appendChild(root);
    try {
      return findUsabilityProblems(root).filter((p) => p.kind === "squeezed-text");
    } finally {
      root.remove();
    }
  }

  const row = (width: number, extra = "", text = TITLE) =>
    `<div class="title" data-w="${width}" data-text-w="240" style="font-size: 12px;" ${extra}>${text}</div>`;

  /** jsdom's Range measures nothing; `data-lines` is how many lines its element's text takes. */
  const rangeProto = Range.prototype as unknown as { getClientRects?: () => DOMRect[] };
  afterEach(() => {
    delete rangeProto.getClientRects;
  });

  beforeEach(() => {
    rangeProto.getClientRects = function (this: Range) {
      const lines = Number((this.startContainer as Element).getAttribute("data-lines") ?? 1);
      // Two boxes a line, as a line holding two spans has.
      return Array.from({ length: lines * 2 }, (_, i) => ({ top: Math.floor(i / 2) * 20, width: 30 }) as DOMRect);
    };
    const size = (el: Element, name: string) => Number(el.getAttribute(name) ?? 800);
    jest.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return { width: size(this, "data-w"), height: 800 } as DOMRect;
    });
    jest.spyOn(Element.prototype, "clientWidth", "get").mockImplementation(function (this: Element) {
      return size(this, "data-w");
    });
    jest.spyOn(Element.prototype, "scrollWidth", "get").mockImplementation(function (this: Element) {
      return Math.max(size(this, "data-w"), Number(this.getAttribute("data-text-w") ?? 0));
    });
  });
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("flags titles cut to a few letters, with the count and the narrowest", () => {
    const found = squeezed(row(47) + row(5) + row(0));
    expect(found).toHaveLength(1);
    expect(found[0].detail).toContain(".title ×3");
    expect(found[0].detail).toContain("0px");
  });

  it("passes the same titles with room to read them", () => {
    expect(squeezed(row(48) + row(230))).toEqual([]);
  });

  it("flags a heading wrapped to a word per line, and one whose text is in a span", () => {
    const tall = (inner: string) =>
      `<h2 data-w="60" data-lines="3" style="font-size: 19px">${inner}</h2>`;
    expect(squeezed(tall("M01 Bootstrap vertical slice"))).toHaveLength(1);
    expect(squeezed(tall(`<span style="display: inline">M01 Bootstrap vertical slice</span>`))).toHaveLength(1);
    expect(squeezed(`<h2 data-w="60" data-lines="2" style="font-size: 19px">M01 Bootstrap</h2>`)).toEqual([]);
  });

  it("measures the text's room, not its padding", () => {
    expect(squeezed(row(60).replace('style="', 'style="padding-left: 10px; padding-right: 10px; '))).toHaveLength(1);
  });

  it("leaves a tall cell whose text fits on two lines", () => {
    expect(squeezed(`<div data-w="40" data-lines="2" style="font-size: 12px">11/16 auto</div>`)).toEqual([]);
  });

  it("leaves a cut label that carries its whole text in title, unless it is a sliver", () => {
    expect(squeezed(row(40, `title="${TITLE}"`))).toEqual([]);
    expect(squeezed(row(40, 'title="Open the ticket"'))).toHaveLength(1);
    expect(squeezed(row(40, `title="${TITLE}" data-lines="3"`))).toHaveLength(1);
    expect(squeezed(row(23, `title="${TITLE}"`))).toHaveLength(1);
  });

  it("leaves short tags, hidden text and the visually-hidden idiom alone", () => {
    expect(squeezed(row(20, "", "P3"))).toEqual([]);
    expect(squeezed(row(5, 'aria-hidden="true"'))).toEqual([]);
    expect(squeezed(row(1).replace('style="', 'style="position: absolute; '))).toEqual([]);
  });

  it("stands aside where nothing is laid out", () => {
    const nothing = { width: 0, height: 0 } as DOMRect;
    jest.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(nothing);
    expect(squeezed(row(5))).toEqual([]);
  });
});
