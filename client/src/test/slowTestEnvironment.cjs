/**
 * A jest test over the per-test ceiling fails, unless it says why it has to be slow.
 *
 * The jest half of server/tests/slow_test_ceiling.py; read that module for why.
 * A test whose own time (its beforeEach hooks, its body, its afterEach hooks:
 * circus's `test.duration`) passes CEILING_MS fails with its measured time and
 * the ceiling, unless:
 *
 * - it was declared with `slowTest(reason, name, fn)` from `src/test/slowTest.ts`,
 *   with a reason that is not blank; or
 * - its id, or its file, is in `src/test/slowTestRatchet.json`, the slow tests
 *   that were already there when this landed. That list may only shrink
 *   (server/tests/test_slow_test_ratchet.py).
 *
 * The id is `<path from client/>::<describe › … › test name>`, which is what the
 * failure message prints, so a ratchet entry can be found by searching for it.
 * An entry with no `::` is a whole file.
 *
 * As on the server, it fails only in CI (`CI=true`), where wall clock is least
 * noisy; anywhere else the same message is written to stderr and nothing fails.
 * A blank `slowTest` reason fails everywhere. Even in CI the ceiling is scaled by
 * the run queue per core: `CEILING_MS * max(1, load1 / cpus)`.
 *
 * This is the configured `testEnvironment` (jest.config.cjs). A file that names
 * another environment in a docblock opts out of it; none does today.
 */
const { realpathSync } = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { TestEnvironment } = require("jest-environment-jsdom");

const inherited = new Set(require("./slowTestRatchet.json").inherited);

/**
 * Milliseconds of hooks + body, idle: 5s, the server's ceiling, and the one jest
 * can prove things about from CI's own logs. CI keeps no per-test jest timings,
 * but its default reporter prints a file's time whenever the file took over 5s
 * (`slowTestThreshold`). So a file it printed no time for ran in under 5s, and
 * none of its tests can be over this ceiling. Measured 2026-11-26 from the
 * "Client (TypeScript)" jobs of runs 45819701261, 45818593089 and 45817899729,
 * after the test speedups (#987): 483 test files, 15 timed over 5s in at least
 * one run (largest 70s). Those 15 files are the ratchet, as whole files, the
 * finest grain CI's logs give. A per-test CI timing would let it name tests
 * instead.
 */
const CEILING_MS = 5000;

/** Where `slowTest` records the functions it was given; shared with slowTest.ts. */
const SLOW_TESTS = Symbol.for("loregarden.slowTests");

const ROOT_DESCRIBE_BLOCK = "ROOT_DESCRIBE_BLOCK";

function loadFactor() {
  const cpus = os.cpus().length || 1;
  return Math.max(1, os.loadavg()[0] / cpus);
}

function testId(relativePath, test) {
  const names = [];
  for (let node = test; node; node = node.parent) {
    if (node.name !== ROOT_DESCRIBE_BLOCK) names.unshift(node.name);
  }
  return `${relativePath}::${names.join(" › ")}`;
}

/**
 * The error to fail a finished test with, or null. Pure, so it is tested without
 * running jest: `id`, `durationMs`, `exempt` (marked or inherited), `factor`.
 */
function overCeilingError({ id, durationMs, exempt, factor, ceilingMs = CEILING_MS }) {
  const ceiling = ceilingMs * factor;
  if (exempt || durationMs == null || durationMs <= ceiling) return null;
  const scaled = factor > 1 ? ` (${ceilingMs}ms x load factor ${factor.toFixed(2)})` : "";
  return new Error(
    `${id} took ${durationMs}ms (hooks + body), over the per-test ceiling of ` +
      `${Math.round(ceiling)}ms${scaled}.\n` +
      'Make it faster, or declare it with slowTest("why it cannot be", name, fn) from ' +
      "src/test/slowTest.ts. Do not add it to src/test/slowTestRatchet.json: that list only shrinks.",
  );
}

class SlowTestEnvironment extends TestEnvironment {
  constructor(config, context) {
    super(config, context);
    // Real paths: a rootDir under a symlink (macOS /var -> /private/var) would
    // otherwise give an id no ratchet entry matches.
    this.relativePath = path
      .relative(realpathSync(config.projectConfig.rootDir), realpathSync(context.testPath))
      .split(path.sep)
      .join("/");
    /** Instance fields so a fixture environment can pin them; nothing else overrides them. */
    this.ceilingMs = CEILING_MS;
    this.inherited = inherited;
    this.loadFactor = loadFactor;
    this.fail = process.env.CI === "true";
  }

  handleTestEvent(event) {
    if (event.name !== "test_done") return;
    const { test } = event;
    if (test.errors.length > 0) return;
    const id = testId(this.relativePath, test);
    const marked = this.global[SLOW_TESTS];
    const exempt =
      this.inherited.has(id) ||
      this.inherited.has(this.relativePath) || (marked !== undefined && marked.has(test.fn));
    const error = overCeilingError({
      id,
      durationMs: test.duration,
      exempt,
      factor: this.loadFactor(),
      ceilingMs: this.ceilingMs,
    });
    if (!error) return;
    if (this.fail) test.errors.push(error);
    else process.stderr.write(`slow test (reported; CI fails this): ${error.message.split("\n")[0]}\n`);
  }
}

module.exports = SlowTestEnvironment;
module.exports.overCeilingError = overCeilingError;
module.exports.testId = testId;
module.exports.CEILING_MS = CEILING_MS;
module.exports.SLOW_TESTS = SLOW_TESTS;
