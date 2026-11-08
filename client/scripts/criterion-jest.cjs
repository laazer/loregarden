#!/usr/bin/env node
/**
 * Rerun the jest tests an acceptance criterion names as its proof, and fail
 * unless every one of them ran and passed.
 *
 * The criteria check before a ticket's gate (server `criterion_check`) calls
 * this once per criterion, from the repository root, with that criterion's
 * client tests as `client/<path>::"exact jest name"`.
 *
 * Plain `jest -t <name>` is not enough on its own: a name that matches nothing
 * (renamed, `it.skip`, only in a comment) is skipped, and jest still exits 0,
 * so a test that never ran would be reported as rerun. Here every named test
 * must appear in jest's report as passed.
 *
 * Exit codes: 0 every named test passed; 1 one failed or did not run;
 * 69 jest could not run at all (no node_modules, no report), which the check
 * reports as an environment problem rather than an unmet criterion.
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const EX_UNAVAILABLE = 69;
const CLIENT_ROOT = path.resolve(__dirname, "..");

/** `client/src/a.test.ts::"its name"` → `{ file: "src/a.test.ts", name: "its name" }`, or null. */
function parseTestId(testId) {
  const at = testId.indexOf("::");
  if (at < 0) return null;
  const file = testId.slice(0, at).trim().replace(/^client\//, "");
  const name = testId
    .slice(at + 2)
    .trim()
    .replace(/^["']|["']$/g, "");
  if (!file || !name) return null;
  return { file: file, name: name };
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Each named test's outcome in a jest `--json` report: "passed", "failed",
 * "pending" (skipped or todo), or "missing" when the report has no such test.
 * A test is matched by its own title, or by its full name with describes.
 */
function outcomes(tests, report) {
  return tests.map(function (test) {
    const suite = (report.testResults || []).find(function (result) {
      return path.resolve(CLIENT_ROOT, test.file) === path.resolve(result.name || "");
    });
    const found = suite
      ? (suite.assertionResults || []).filter(function (a) {
          return a.title === test.name || a.fullName === test.name;
        })
      : [];
    if (!found.length) return { test: test, status: "missing" };
    const failed = found.find(function (a) {
      return a.status !== "passed";
    });
    return { test: test, status: failed ? failed.status : "passed" };
  });
}

function main(argv) {
  const tests = argv.map(parseTestId);
  if (!tests.length || tests.some((t) => t === null)) {
    process.stderr.write("usage: criterion-jest.cjs 'client/<path>::\"exact jest name\"'...\n");
    return EX_UNAVAILABLE;
  }
  const jest = path.join(CLIENT_ROOT, "node_modules", ".bin", "jest");
  if (!fs.existsSync(jest)) {
    process.stderr.write("jest is not installed under client/node_modules; nothing can rerun\n");
    return EX_UNAVAILABLE;
  }
  const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "criterion-jest-")), "report.json");
  const files = Array.from(new Set(tests.map((t) => t.file)));
  const pattern = tests.map((t) => escapeRegExp(t.name)).join("|");
  const workers = process.env.LOREGARDEN_CAPACITY_WORKERS || "1";
  spawnSync(
    jest,
    ["--ci", "--json", "--outputFile", out, "--maxWorkers", workers, "-t", pattern, "--runTestsByPath"].concat(
      files,
    ),
    { cwd: CLIENT_ROOT, stdio: ["ignore", "inherit", "inherit"] },
  );
  if (!fs.existsSync(out)) {
    process.stderr.write("jest wrote no report, so no test can be said to have run\n");
    return EX_UNAVAILABLE;
  }
  const results = outcomes(tests, JSON.parse(fs.readFileSync(out, "utf8")));
  results.forEach(function (r) {
    process.stdout.write(r.status + ": " + r.test.file + '::"' + r.test.name + '"\n');
  });
  return results.every((r) => r.status === "passed") ? 0 : 1;
}

module.exports = { parseTestId: parseTestId, outcomes: outcomes, EX_UNAVAILABLE: EX_UNAVAILABLE };

if (require.main === module) {
  process.exitCode = main(process.argv.slice(2));
}
