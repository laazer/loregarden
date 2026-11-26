/**
 * Declare a test that may exceed the per-test ceiling, and say why.
 *
 * `slowTestEnvironment.cjs` fails a test over its ceiling unless it was declared
 * here (or is inherited in slowTestRatchet.json, a list that only shrinks). The
 * reason is required and must not be blank: a blank one fails the test, fast or
 * not, the way `@pytest.mark.slow(reason="")` does on the server.
 */

/** Must match SLOW_TESTS in slowTestEnvironment.cjs, which reads this set. */
const SLOW_TESTS = Symbol.for("loregarden.slowTests");

type Registry = WeakSet<object>;
type SlowTestFn = () => void | PromiseLike<unknown>;

function registry(): Registry {
  const holder = globalThis as unknown as Record<symbol, Registry | undefined>;
  const existing = holder[SLOW_TESTS];
  if (existing) return existing;
  const created: Registry = new WeakSet();
  holder[SLOW_TESTS] = created;
  return created;
}

export function slowTest(reason: string, name: string, fn: SlowTestFn, timeout?: number): void {
  if (reason.trim() === "") {
    test(name, () => {
      throw new Error(
        `slowTest("${name}") needs a non-empty reason saying why this test cannot be faster`,
      );
    });
    return;
  }
  // A wrapper of its own, so the mark names this test and not every test that
  // shares `fn`. Arity 0: a done-callback test is not supported here.
  const marked = function (this: unknown) {
    return fn.call(this);
  };
  registry().add(marked);
  // jest types a body as either a done-callback or a promise; `marked` is one
  // or the other at runtime, the same as the `fn` it forwards to.
  test(name, marked as Parameters<typeof test>[1], timeout);
}
