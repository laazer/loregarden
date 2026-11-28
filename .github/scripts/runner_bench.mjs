#!/usr/bin/env node
// How fast this runner is, measured by work no pull request changes.
//
//   node .github/scripts/runner_bench.mjs > runner.json
//
// The suite-time check scales main's baseline by the runner's speed, read as the
// median of each test file's time against main's. A change that slows every
// file alike moves that median too, so it read as a slow runner. This is a
// second reading of the same runner, from fixed work: when the files ran slower
// than this says the runner is, the difference is the change's (suite_timing.py).
//
// Shaped like jest's run: a fixed set of tasks spread over cores - 1 worker
// threads (jest's default pool), each task the kind of work a test file costs
// (building strings, JSON, regular expressions, sorting, maps). The fastest of
// a few rounds is reported, since a runner is never faster than it is, after
// one round that is thrown away: the first pays for the JIT warming up.
//
// Change the work and the readings stop comparing with main's: bump VERSION, and
// the check ignores main's runs of the old one until enough of the new one exist.

import { availableParallelism } from "node:os";
import { Worker, isMainThread, parentPort } from "node:worker_threads";
import { fileURLToPath } from "node:url";

const VERSION = 1;
const TASKS = 96;
const ROUNDS = 5;
const TASK_SIZE = 12000;

function task(seed) {
  let state = seed;
  const next = () => {
    state = (state * 1103515245 + 12345) % 2147483648;
    return state;
  };
  const rows = [];
  for (let i = 0; i < TASK_SIZE; i++) {
    rows.push({ id: next(), name: `row-${next().toString(36)}-${i}`, tags: [next() % 7, next() % 11] });
  }
  const copied = JSON.parse(JSON.stringify(rows));
  const counts = new Map();
  for (const row of copied) {
    const key = row.name.replace(/[0-9]+/g, "#").slice(0, 8);
    counts.set(key, (counts.get(key) ?? 0) + row.tags[0]);
  }
  copied.sort((a, b) => a.id - b.id);
  return counts.size + copied[0].id;
}

if (!isMainThread) {
  parentPort.on("message", (seed) => parentPort.postMessage(task(seed)));
} else {
  const workers = Math.max(1, availableParallelism() - 1);
  const pool = Array.from({ length: workers }, () => new Worker(fileURLToPath(import.meta.url)));

  const round = () =>
    new Promise((resolve, reject) => {
      const started = performance.now();
      let issued = 0;
      let done = 0;
      const feed = (worker) => {
        if (issued < TASKS) worker.postMessage(issued++ + 1);
      };
      for (const worker of pool) {
        worker.removeAllListeners("message");
        worker.removeAllListeners("error");
        worker.on("error", reject);
        worker.on("message", () => {
          done += 1;
          if (done === TASKS) resolve(performance.now() - started);
          else feed(worker);
        });
        feed(worker);
      }
    });

  const times = [];
  try {
    await round();
    for (let i = 0; i < ROUNDS; i++) times.push(await round());
  } finally {
    await Promise.all(pool.map((worker) => worker.terminate()));
  }
  const ms = Math.min(...times);
  process.stdout.write(
    JSON.stringify({ version: VERSION, ms: Math.round(ms), rounds: times.map(Math.round), workers }) + "\n",
  );
}
