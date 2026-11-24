import { followScreenArea } from "../screenArea";

class FakeResizeObserver {
  static instances: FakeResizeObserver[] = [];
  observed = new Set<Element>();
  private readonly callback: () => void;
  constructor(callback: () => void) {
    this.callback = callback;
    FakeResizeObserver.instances.push(this);
  }
  observe = (node: Element) => void this.observed.add(node);
  unobserve = (node: Element) => void this.observed.delete(node);
  disconnect = () => this.observed.clear();
  fire = () => this.callback();
}

/** A page area whose top is `top` while it is in the document, and 0 once removed, as in a browser. */
function mountArea(top: number): HTMLElement {
  const area = document.createElement("div");
  area.className = "screen-area";
  area.getBoundingClientRect = () => ({ top: area.isConnected ? top : 0 }) as DOMRect;
  document.body.append(area);
  return area;
}

describe("followScreenArea", () => {
  const original = globalThis.ResizeObserver;

  beforeEach(() => {
    FakeResizeObserver.instances = [];
    globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;
  });

  afterEach(() => {
    globalThis.ResizeObserver = original;
    document.body.replaceChildren();
  });

  it.each([
    ["the removed node reports a size change", () => FakeResizeObserver.instances[0].fire()],
    ["the window resizes", () => window.dispatchEvent(new Event("resize"))],
  ])("measures the remounted area when %s", (_label, trigger) => {
    const tops: number[] = [];
    const first = mountArea(44);
    const stop = followScreenArea((rect) => tops.push(rect.top));

    first.remove();
    const second = mountArea(44);
    trigger();

    expect(tops).toEqual([44, 44]);
    expect([...FakeResizeObserver.instances[0].observed]).toEqual([second]);
    stop();
  });

  it("stops measuring once stopped", () => {
    mountArea(44);
    const onMeasure = jest.fn();
    followScreenArea(onMeasure)();

    window.dispatchEvent(new Event("resize"));

    expect(onMeasure).toHaveBeenCalledTimes(1);
    expect(FakeResizeObserver.instances[0].observed.size).toBe(0);
  });
});
