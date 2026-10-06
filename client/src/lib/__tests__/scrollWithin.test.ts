import { scrollIntoScroller, scrollIntoScrollerInline, scrollScrollerToBottom } from "../scrollWithin";

/** frame (overflow: hidden) > scroller (overflow-y: auto) > target. */
function build(targetRect: Partial<DOMRect> = { top: 700, bottom: 900 }) {
  const frame = document.createElement("div");
  frame.style.overflow = "hidden";
  const scroller = document.createElement("div");
  scroller.style.overflowY = "auto";
  const target = document.createElement("div");
  scroller.appendChild(target);
  frame.appendChild(scroller);
  document.body.appendChild(frame);
  Object.defineProperty(scroller, "scrollHeight", { value: 2000, configurable: true });
  scroller.scrollTop = 100;
  scroller.getBoundingClientRect = () => ({ top: 50, bottom: 450 }) as DOMRect;
  target.getBoundingClientRect = () => targetRect as DOMRect;
  return { frame, scroller, target };
}

const scrollTo = jest.fn();
const scrollIntoView = jest.fn();

beforeEach(() => {
  scrollTo.mockClear();
  scrollIntoView.mockClear();
  Element.prototype.scrollTo = scrollTo as unknown as Element["scrollTo"];
  Element.prototype.scrollIntoView = scrollIntoView;
});

afterEach(() => {
  delete (Element.prototype as Partial<Element>).scrollTo;
  delete (Element.prototype as Partial<Element>).scrollIntoView;
  document.body.innerHTML = "";
});

describe("scrollWithin", () => {
  test("lines a node's top up with its scroller's top, moving nothing else", () => {
    const { scroller, target } = build();
    scrollIntoScroller(target, "start");
    // 100 already scrolled + (700 - 50) to bring the top edge up.
    expect(scrollTo.mock.instances).toEqual([scroller]);
    expect(scrollTo).toHaveBeenCalledWith({ top: 750, behavior: "auto" });
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  test("lines a node's foot up with its scroller's foot", () => {
    const { target } = build();
    scrollIntoScroller(target, "end");
    expect(scrollTo).toHaveBeenCalledWith({ top: 550, behavior: "auto" });
  });

  test("scrolls a scroller to its bottom", () => {
    const { scroller, target } = build();
    scrollScrollerToBottom(target, "smooth");
    expect(scrollTo.mock.instances).toEqual([scroller]);
    expect(scrollTo).toHaveBeenCalledWith({ top: 2000, behavior: "smooth" });
  });

  test("an overflow:hidden ancestor is never scrolled — with no real scroller nothing moves", () => {
    const frame = document.createElement("div");
    frame.style.overflow = "hidden";
    const target = document.createElement("div");
    frame.appendChild(target);
    document.body.appendChild(frame);
    scrollIntoScroller(target, "start");
    scrollScrollerToBottom(target);
    expect(scrollTo).not.toHaveBeenCalled();
  });

  describe("nearest", () => {
    // The scroller shows 50..450 and is already 100 down.
    test("a node already in view moves nothing", () => {
      const { target } = build({ top: 100, bottom: 200 });
      scrollIntoScroller(target, "nearest");
      expect(scrollTo).not.toHaveBeenCalled();
    });

    test("a node below the fold is brought up only until its foot shows", () => {
      const { scroller, target } = build({ top: 700, bottom: 900 });
      scrollIntoScroller(target, "nearest", "smooth");
      expect(scrollTo.mock.instances).toEqual([scroller]);
      expect(scrollTo).toHaveBeenCalledWith({ top: 550, behavior: "smooth" });
    });

    test("a node above the fold is brought down only until its top shows", () => {
      const { target } = build({ top: -100, bottom: 0 });
      scrollIntoScroller(target, "nearest");
      expect(scrollTo).toHaveBeenCalledWith({ top: -50, behavior: "auto" });
    });

    test("a node taller than the view, hanging off the foot, shows its top", () => {
      const { target } = build({ top: 300, bottom: 1000 });
      scrollIntoScroller(target, "nearest");
      expect(scrollTo).toHaveBeenCalledWith({ top: 350, behavior: "auto" });
    });

    test("a node that already covers the whole view moves nothing", () => {
      const { target } = build({ top: 0, bottom: 600 });
      scrollIntoScroller(target, "nearest");
      expect(scrollTo).not.toHaveBeenCalled();
    });
  });

  test("centres a node in its scroller", () => {
    const { target } = build({ top: 700, bottom: 900 });
    scrollIntoScroller(target, "center");
    // Node centre 800, view centre 250.
    expect(scrollTo).toHaveBeenCalledWith({ top: 650, behavior: "auto" });
  });

  test("on the inline axis, scrolls the sideways strip and not the column around it", () => {
    const column = document.createElement("div");
    column.style.overflowY = "auto";
    const strip = document.createElement("div");
    strip.style.overflowX = "auto";
    strip.style.overflowY = "hidden";
    const tab = document.createElement("div");
    strip.appendChild(tab);
    column.appendChild(strip);
    document.body.appendChild(column);
    strip.scrollLeft = 20;
    strip.getBoundingClientRect = () => ({ left: 0, right: 300 }) as DOMRect;
    tab.getBoundingClientRect = () => ({ left: 350, right: 450 }) as DOMRect;

    scrollIntoScrollerInline(tab, "nearest");
    expect(scrollTo.mock.instances).toEqual([strip]);
    expect(scrollTo).toHaveBeenCalledWith({ left: 170, behavior: "auto" });

    scrollTo.mockClear();
    scrollIntoScrollerInline(tab, "center");
    // Tab centre 400, strip centre 150.
    expect(scrollTo).toHaveBeenCalledWith({ left: 270, behavior: "auto" });
    expect(scrollIntoView).not.toHaveBeenCalled();
  });
});
