import { scrollIntoScroller, scrollScrollerToBottom } from "../scrollWithin";

/** frame (overflow: hidden) > scroller (overflow-y: auto) > target. */
function build() {
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
  target.getBoundingClientRect = () => ({ top: 700, bottom: 900 }) as DOMRect;
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
});
