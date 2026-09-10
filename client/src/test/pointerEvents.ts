/**
 * Pointer events for a jsdom spec file.
 *
 * Lifted out of `viewHarness` when a second surface — the utility dock's resize
 * grip — needed the same thing without the view harness's fake server, rect stub
 * and toast reset. The reasoning below is that harness's, kept verbatim because
 * it is the part that is easy to get subtly wrong.
 *
 * Installed per spec file rather than globally in `test/setup.ts`. A global
 * definition collides with a suite that installs its own — and if it is defined
 * with `Object.defineProperty`'s defaults it is non-writable, so the colliding
 * assignment throws in `beforeAll` and takes the whole file with it.
 */

/**
 * jsdom implements no `PointerEvent`, and RTL's `fireEvent.pointerDown` falls
 * back to a bare `Event` when the constructor is missing — which silently drops
 * `clientX`, making every drag below a drag to the origin. The subclass is the
 * smallest thing that carries coordinates and a pointer id.
 */
export class FakePointerEvent extends MouseEvent {
  readonly pointerId: number;
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 1;
  }
}

/** Install `PointerEvent` and a tracking capture API for this spec file. */
export function installPointerEvents(): void {
  beforeAll(() => {
    (globalThis as unknown as { PointerEvent: unknown }).PointerEvent = FakePointerEvent;
    // jsdom implements none of the capture API; a renderer that captures the
    // pointer would otherwise die on `undefined is not a function` rather than
    // fail the assertion that wants it.
    //
    // These track rather than shrug, because a renderer may *ask* whether it
    // still holds a pointer before releasing it (the release throws
    // `NotFoundError` otherwise): a stub that always answered `true` would hide
    // the guard and a stub that always answered `false` would hide the release.
    // `captured` is the smallest thing that answers honestly.
    const captured = new WeakMap<Element, Set<number>>();
    Element.prototype.setPointerCapture = function setPointerCapture(pointerId: number) {
      const held = captured.get(this) ?? new Set<number>();
      held.add(pointerId);
      captured.set(this, held);
    };
    Element.prototype.hasPointerCapture = function hasPointerCapture(pointerId: number) {
      return captured.get(this)?.has(pointerId) ?? false;
    };
    Element.prototype.releasePointerCapture = function releasePointerCapture(pointerId: number) {
      const held = captured.get(this);
      // The real one throws rather than shrugging, and a test that never sees the
      // throw cannot tell a guarded release from an unguarded one.
      if (held === undefined || !held.has(pointerId)) {
        throw new DOMException("No active pointer with the given id", "NotFoundError");
      }
      held.delete(pointerId);
    };
  });
}
