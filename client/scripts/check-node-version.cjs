#!/usr/bin/env node
/**
 * Exit non-zero, naming both versions, when the running Node does not satisfy
 * client/package.json `engines.node` — the one place the requirement lives.
 *
 * Run before the toolchain, so it runs under exactly the Node that is too old:
 * Node 14 meets Vite 8 with `SyntaxError: Unexpected token '??='` and exits 0.
 * Hence plain CommonJS and no syntax newer than ES2015 here, and no
 * dependencies — `semver` is only ever a hoisted transitive package.
 *
 * Supports the range grammar engines fields actually use: `||`-separated sets
 * of space-separated comparators, each `^`, `~`, `>=`, `>`, `<=`, `<`, `=` or
 * bare, on a full or partial version. Anything else is an error, not a pass.
 *
 * Exit codes: 0 supported, 1 unsupported, 2 the range or version is unreadable.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const VERSION_RE = /^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/;
const COMPARATOR_RE = /^(\^|~|>=|>|<=|<|=)?(.+)$/;

/** Parse `v22.13.0`, `22.13`, `23.0.0-nightly2024`… into parts; `null` if it is not a version. */
function parseVersion(text) {
  const match = VERSION_RE.exec(text);
  if (!match) return null;
  return {
    major: Number(match[1]),
    minor: match[2] === undefined ? null : Number(match[2]),
    patch: match[3] === undefined ? null : Number(match[3]),
    prerelease: match[4] === undefined ? [] : match[4].split("."),
  };
}

function compareIdentifiers(a, b) {
  const aNum = /^\d+$/.test(a);
  const bNum = /^\d+$/.test(b);
  if (aNum && bNum) return Number(a) - Number(b);
  if (aNum) return -1;
  if (bNum) return 1;
  return a < b ? -1 : a > b ? 1 : 0;
}

/** Semver precedence: a prerelease sorts below its release (22.13.0-rc.1 < 22.13.0). */
function compareVersions(a, b) {
  const fields = ["major", "minor", "patch"];
  for (let i = 0; i < fields.length; i += 1) {
    const diff = (a[fields[i]] || 0) - (b[fields[i]] || 0);
    if (diff !== 0) return diff;
  }
  if (a.prerelease.length === 0 || b.prerelease.length === 0) {
    return b.prerelease.length - a.prerelease.length;
  }
  for (let i = 0; i < Math.max(a.prerelease.length, b.prerelease.length); i += 1) {
    if (a.prerelease[i] === undefined) return -1;
    if (b.prerelease[i] === undefined) return 1;
    const diff = compareIdentifiers(a.prerelease[i], b.prerelease[i]);
    if (diff !== 0) return diff;
  }
  return 0;
}

function bound(major, minor, patch) {
  return { major: major, minor: minor, patch: patch, prerelease: [] };
}

/** One comparator to a list of `{op, version}` bounds, all of which must hold. */
function parseComparator(text, range) {
  const match = COMPARATOR_RE.exec(text);
  const op = match[1] || "";
  const v = parseVersion(match[2]);
  if (!v || (v.prerelease.length > 0 && (v.minor === null || v.patch === null))) {
    throw new Error("unreadable comparator \"" + text + "\" in range \"" + range + "\"");
  }
  const lower = bound(v.major, v.minor || 0, v.patch || 0);
  lower.prerelease = v.prerelease;
  if (op === ">=" || op === ">" || op === "<=" || op === "<") {
    return [{ op: op, version: lower }];
  }
  if (op === "~") {
    const upper = v.minor === null ? bound(v.major + 1, 0, 0) : bound(v.major, v.minor + 1, 0);
    return [{ op: ">=", version: lower }, { op: "<", version: upper }];
  }
  if (op === "^") {
    let upper;
    if (v.major > 0 || v.minor === null) upper = bound(v.major + 1, 0, 0);
    else if (v.minor > 0 || v.patch === null) upper = bound(0, v.minor + 1, 0);
    else upper = bound(0, 0, v.patch + 1);
    return [{ op: ">=", version: lower }, { op: "<", version: upper }];
  }
  // Bare or `=`: a full version is exact, a partial one is that whole line.
  if (v.minor === null) return [{ op: ">=", version: lower }, { op: "<", version: bound(v.major + 1, 0, 0) }];
  if (v.patch === null) return [{ op: ">=", version: lower }, { op: "<", version: bound(v.major, v.minor + 1, 0) }];
  return [{ op: "=", version: lower }];
}

function holds(version, comparator) {
  const diff = compareVersions(version, comparator.version);
  switch (comparator.op) {
    case ">=": return diff >= 0;
    case ">": return diff > 0;
    case "<=": return diff <= 0;
    case "<": return diff < 0;
    default: return diff === 0;
  }
}

/**
 * Whether `version` (e.g. `process.version`) is inside the engines `range`.
 * Throws on a range or version it cannot read, so a typo can never pass.
 */
function satisfies(version, range) {
  const parsed = parseVersion(String(version).trim());
  if (!parsed || parsed.minor === null || parsed.patch === null) {
    throw new Error("unreadable Node version \"" + version + "\"");
  }
  const sets = String(range).split("||").map((set) => set.trim());
  if (sets.some((set) => set === "")) {
    throw new Error("unreadable range \"" + range + "\"");
  }
  return sets.some((set) =>
    set
      .split(/\s+/)
      .map((text) => parseComparator(text, range))
      .every((bounds) => bounds.every((c) => holds(parsed, c))),
  );
}

function readRequiredRange(packageJsonPath) {
  const manifest = JSON.parse(fs.readFileSync(packageJsonPath, "utf8"));
  const range = manifest.engines && manifest.engines.node;
  if (typeof range !== "string" || range.trim() === "") {
    throw new Error(packageJsonPath + " declares no engines.node");
  }
  return range;
}

function main() {
  const packageJsonPath = path.join(__dirname, "..", "package.json");
  let range;
  let ok;
  try {
    range = readRequiredRange(packageJsonPath);
    ok = satisfies(process.version, range);
  } catch (error) {
    process.stderr.write("cannot check the Node version: " + error.message + "\n");
    return 2;
  }
  if (!ok) {
    process.stderr.write(
      "Node " + process.version + " is not supported; client/package.json engines.node requires \"" + range + "\"\n",
    );
    return 1;
  }
  return 0;
}

module.exports = { satisfies: satisfies, parseVersion: parseVersion, readRequiredRange: readRequiredRange };

if (require.main === module) {
  process.exitCode = main();
}
