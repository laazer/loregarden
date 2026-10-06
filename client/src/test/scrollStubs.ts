/**
 * Scrolling, stood in for jsdom — which implements none of it and loads no CSS.
 *
 * `scrollWithin` finds a node's scroller by computed `overflow`, then measures
 * both boxes, so a call-site test needs three things jsdom lacks: the scroller's
 * overflow rule, the boxes' geometry, and the scroll methods to record. `css` is
 * injected as a real `<style>` (jsdom does cascade those); `rects` gives each
 * element its box, or `null` for jsdom's all-zero default.
 *
 * `scrollIntoView` is recorded too, so a test can say it was *not* reached for:
 * that is the call that scrolled the app frame.
 */
export function stubScrolling(css: string, rects: (element: Element) => Partial<DOMRect> | null) {
  const scrollTo = jest.fn();
  const scrollIntoView = jest.fn();
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);
  const getBoundingClientRect = jest
    .spyOn(Element.prototype, "getBoundingClientRect")
    .mockImplementation(function (this: Element) {
      const zero = { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0 };
      return { ...zero, ...rects(this) } as DOMRect;
    });
  Element.prototype.scrollTo = scrollTo as unknown as Element["scrollTo"];
  Element.prototype.scrollIntoView = scrollIntoView;
  return {
    scrollTo,
    scrollIntoView,
    restore() {
      style.remove();
      getBoundingClientRect.mockRestore();
      delete (Element.prototype as Partial<Element>).scrollTo;
      delete (Element.prototype as Partial<Element>).scrollIntoView;
    },
  };
}
