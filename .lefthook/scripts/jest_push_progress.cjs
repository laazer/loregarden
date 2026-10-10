/**
 * Jest reporter: count finished test files into the pre-push progress file.
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

/** Writes per second, at most. The holder reads the file every couple of seconds. */
const MIN_INTERVAL_MS = 1000;

class PushProgressReporter {
  constructor() {
    this.path = process.env.LOREGARDEN_PROGRESS_FILE || "";
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
    this.total = results.numTotalTestSuites;
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
