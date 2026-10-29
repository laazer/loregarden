/**
 * Jest reporter: what this run selected, and how far through it is.
 *
 * Two files, two readers. $LOREGARDEN_TEST_COUNT_FILE is written always, holds
 * {"count": N} from jest's own numTotalTestSuites, and is what
 * judge_test_run.py reads after jest exits to decide whether the push verified
 * anything; it fails CLOSED, so a count that could not be written leaves no
 * file and reads as "could not run" rather than as a legitimate zero.
 * $LOREGARDEN_PROGRESS_FILE is the queue board's view, and only when a capacity
 * holder named one.
 *
 * client-tests.sh adds it beside jest's default reporter. When the run is held
 * under `loregarden capacity run`, $LOREGARDEN_PROGRESS_FILE names a file the
 * holder copies onto the lease (server/loregarden/services/capacity_progress.py),
 * so the queue board and every push waiting behind this one read "38/412"
 * instead of "holding". Without that variable it does nothing.
 *
 * Files, not tests: jest only knows how many files it will run up front.
 */
const fs = require("node:fs");

/** The count the pre-push judge reads. Written only here and by the pytest plugin. */
const COUNT_ENV = "LOREGARDEN_TEST_COUNT_FILE";
/** The queue board's file: same inheritance, same rule. */
const PROGRESS_ENV = "LOREGARDEN_PROGRESS_FILE";

/**
 * Take an inherited path and stop it being inherited again.
 *
 * Every child this run starts copies `process.env`, so a path left there is a
 * path a nested runner writes over — and the pre-push script EXPORTS the count
 * path. Reading it once, here in the process that owns it, is the structural
 * half of C-4; claiming the file by creating it exclusively is the half that
 * survives an environment some child rebuilt by hand.
 */
function take(name) {
  const value = process.env[name] || "";
  delete process.env[name];
  return value;
}

const COUNT_PATH = take(COUNT_ENV);
const PROGRESS_PATH = take(PROGRESS_ENV);

/**
 * Record the suite count jest itself reported, or say why it was not recorded.
 *
 * Only a non-negative integer is a count. `undefined`, `null` and the string
 * "286" are jest API drift, not numbers, and recording them would turn a drift
 * into a number the judge trusts — so they fail closed and leave no file.
 */
function recordSuiteCount(total) {
  const path = COUNT_PATH;
  if (!path) return;
  if (!Number.isInteger(total) || total < 0) {
    process.stderr.write(
      `jest_push_progress: jest reported no usable test suite count ` +
        `(${JSON.stringify(total)}), so no count was recorded in ${path}\n`,
    );
    return;
  }
  try {
    // "wx" claims the path: it fails when the file already exists, so the first
    // run to record owns the number and no later runner — nested, inherited or
    // racing — replaces it. One short write, read only after jest has exited.
    fs.writeFileSync(path, JSON.stringify({ count: total }), { flag: "wx" });
  } catch (error) {
    const taken = error && error.code === "EEXIST";
    process.stderr.write(
      taken
        ? `jest_push_progress: ${path} already holds a count, so this run's ` +
            `${total} suite(s) were not recorded there\n`
        : `jest_push_progress: cannot record the test suite count in ${path}: ${error}\n`,
    );
  }
}

/** Writes per second, at most. The holder reads the file every couple of seconds. */
const MIN_INTERVAL_MS = 1000;

class PushProgressReporter {
  constructor() {
    this.path = PROGRESS_PATH;
    this.step = process.env.PUSH_PROGRESS_STEP || "jest";
    this.done = 0;
    this.total = null;
    this.writtenAt = 0;
    this.warned = false;
  }

  write(force) {
    if (!this.path) return;
    const now = Date.now();
    if (!force && now - this.writtenAt < MIN_INTERVAL_MS) return;
    this.writtenAt = now;
    const tmp = `${this.path}.${process.pid}.tmp`;
    const body = JSON.stringify({ step: this.step, done: this.done, total: this.total });
    try {
      fs.writeFileSync(tmp, body);
      fs.renameSync(tmp, this.path);
    } catch (error) {
      // Progress is a view of the run; a view that fails must not fail the
      // tests. Said once, so the board's stale count is explained.
      if (!this.warned) {
        this.warned = true;
        process.stderr.write(`jest_push_progress: cannot write ${this.path}: ${error}\n`);
      }
    }
  }

  onRunStart(results) {
    const total = results && results.numTotalTestSuites;
    recordSuiteCount(total);
    this.total = Number.isInteger(total) ? total : null;
    this.write(true);
  }

  onTestFileResult() {
    this.done += 1;
    this.write(false);
  }

  onRunComplete() {
    this.write(true);
  }
}

module.exports = PushProgressReporter;
