#!/usr/bin/env node
/**
 * Every client module is in the app, or says which open work it is waiting for.
 *
 * A module nothing imports looks the same whether it was abandoned or is half
 * of a ticket nobody has finished. The Pixi Hive floor was offered for deletion
 * on an import search, while lg-hive-workplace-72 had criteria naming it. So the
 * link is written in the module itself, where the person about to delete it
 * reads it, and where `loregarden_get_ticket` finds it for whoever takes the
 * ticket (`kept_code`):
 *
 *     // kept-for: lg-hive-workplace-72 - the Pixi floor its click criteria need
 *
 * Whole-tree rather than diff-scoped: what makes a module unreachable is
 * usually a deleted import in some *other* file.
 *
 * A module under the TypeScript root is in use when the import graph reaches it
 * from a `<script type="module">` in the client's index.html, or when it only
 * augments types (`declare module`/`declare global`). Otherwise it needs one of:
 *
 *   kept-for: <ticket external id> - <reason>   unfinished work; ticket state is
 *                                               `loregarden_doctor`'s to check
 *   used-by: <repo path> - <reason>             a script or a registry test, which
 *                                               must exist and name the module
 *
 * Whatever a marked module imports is covered by it. A `kept-for` on a module
 * the app does reach is a finding too: the work arrived, so the marker goes.
 */

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const { EX_UNAVAILABLE, requireClientModule } = require("./gate_client_modules.cjs");
const { isTestFile, parseArgv, tsSourceRoot } = require("./ts_git_diff.cjs");

const SOURCE = /\.tsx?$/;
const MIN_REASON_CHARS = 12;
const MARKER_LINE = /^\s*(?:\/\/|\/?\*+)\s*(kept-for|used-by):\s*(.*?)\s*(?:\*\/)?\s*$/;
const MARKER_VALUE = /^(\S+)\s+-\s+(.+)$/;
const TICKET_ID = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*-\d+$/;
const MODULE_SCRIPT = /<script\b[^>]*\bsrc=["']\/?([^"']+\.tsx?)["']/g;

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Not `ts_git_diff.git`: it answers a failure with "", which here reads as an empty tree. */
function lsFiles(args, repoRoot) {
  try {
    return execFileSync("git", ["ls-files", "-z", ...args], {
      cwd: repoRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (err) {
    console.error(`ts_unreachable_check: git could not list ${repoRoot}: ${err.message}`);
    process.exit(EX_UNAVAILABLE);
  }
}

function listSources(repoRoot, sourceRoot, diffScope) {
  // A commit is graded on the files git knows about: a half-written module left
  // untracked is not part of it. Every other scope sees the whole tree.
  const flags = diffScope === "staged" ? ["-c"] : ["-c", "-o", "--exclude-standard"];
  const rel = path.relative(repoRoot, sourceRoot);
  return lsFiles([...flags, "--", rel], repoRoot)
    .split("\0")
    .filter((p) => SOURCE.test(p) && !p.endsWith(".d.ts"))
    .map((p) => path.resolve(repoRoot, p))
    .filter((p) => fs.existsSync(p));
}

function isTestSupport(file, sourceRoot) {
  return isTestFile(file) || path.relative(sourceRoot, file).split(path.sep)[0] === "test";
}

function entries(sourceRoot) {
  const clientRoot = path.dirname(sourceRoot);
  const html = path.join(clientRoot, "index.html");
  if (!fs.existsSync(html)) return [];
  const found = [];
  for (const match of fs.readFileSync(html, "utf8").matchAll(MODULE_SCRIPT)) {
    const file = path.resolve(clientRoot, match[1]);
    if (fs.existsSync(file)) found.push(file);
  }
  return found;
}

function compilerOptions(ts, clientRoot) {
  const options = { allowImportingTsExtensions: true };
  for (const name of ["tsconfig.app.json", "tsconfig.json"]) {
    const file = path.join(clientRoot, name);
    if (!fs.existsSync(file)) continue;
    const { config } = ts.readConfigFile(file, ts.sys.readFile);
    if (!config?.compilerOptions) continue;
    const parsed = ts.convertCompilerOptionsFromJson(config.compilerOptions, clientRoot, file);
    Object.assign(options, parsed.options);
    break;
  }
  return { ...options, moduleResolution: ts.ModuleResolutionKind.Bundler };
}

/** Modules a source file names: imports, re-exports, import(), require(), and worker URLs. */
function specifiers(ts, content, rel, errors) {
  const names = ts.preProcessFile(content, true, true).importedFiles.map((f) => f.fileName);
  for (const match of content.matchAll(
    /new URL\(\s*["']([^"']+)["']\s*,\s*import\.meta\.url\s*\)/g
  )) {
    names.push(match[1]);
  }
  for (const match of content.matchAll(/import\.meta\.glob(?:<[^>]*>)?\(([^)]*)\)/g)) {
    if (/\.(?:\{[^}]*)?tsx?\b/.test(match[1])) {
      errors.push(
        `${rel}: import.meta.glob over TypeScript sources is not followed by this gate, so ` +
          "what it loads would read as unused. Import the modules by name."
      );
    }
  }
  return names;
}

function buildGraph(ts, files, sourceRoot, repoRoot, errors) {
  const options = compilerOptions(ts, path.dirname(sourceRoot));
  const cache = ts.createModuleResolutionCache(sourceRoot, (p) => p, options);
  const known = new Set(files);
  const edges = new Map();
  const contents = new Map();
  for (const file of files) {
    const content = fs.readFileSync(file, "utf8");
    contents.set(file, content);
    const targets = new Set();
    for (const name of specifiers(ts, content, path.relative(repoRoot, file), errors)) {
      const hit = ts.resolveModuleName(name, file, options, ts.sys, cache).resolvedModule;
      if (hit && known.has(path.resolve(hit.resolvedFileName))) {
        targets.add(path.resolve(hit.resolvedFileName));
      }
    }
    edges.set(file, targets);
  }
  return { edges, contents };
}

function reach(edges, starts) {
  const seen = new Set(starts);
  const queue = [...starts];
  while (queue.length > 0) {
    for (const next of edges.get(queue.pop()) ?? []) {
      if (!seen.has(next)) {
        seen.add(next);
        queue.push(next);
      }
    }
  }
  return seen;
}

/** A module that exists for its `declare module`/`declare global` is loaded by tsc, not imported. */
function augmentsTypes(ts, file, content) {
  return ts
    .createSourceFile(file, content, ts.ScriptTarget.Latest)
    .statements.some(
      (s) =>
        ts.isModuleDeclaration(s) &&
        (ts.isStringLiteral(s.name) || (s.flags & ts.NodeFlags.GlobalAugmentation) !== 0)
    );
}

function markersIn(content, file, repoRoot, errors) {
  const rel = path.relative(repoRoot, file);
  const markers = [];
  content.split("\n").forEach((line, i) => {
    const hit = MARKER_LINE.exec(line);
    if (!hit) return;
    const [, kind, rest] = hit;
    const at = `${rel}:${i + 1}`;
    const value = MARKER_VALUE.exec(rest);
    if (!value || value[2].trim().length < MIN_REASON_CHARS) {
      errors.push(
        `${at}: \`${kind}:\` needs a ${kind === "kept-for" ? "ticket id" : "repo path"} and a reason: ` +
          `\`${kind}: ${kind === "kept-for" ? "lg-hive-workplace-72" : "client/scripts/x.mjs"} - why\``
      );
      return;
    }
    if (kind === "kept-for" && !TICKET_ID.test(value[1])) {
      errors.push(
        `${at}: \`kept-for: ${value[1]}\` is not a ticket's external id (like lg-hive-workplace-72). ` +
          "A bare number names one ticket per workspace."
      );
      return;
    }
    if (kind === "used-by") {
      const user = path.resolve(repoRoot, value[1]);
      const name = path.basename(file).replace(SOURCE, "");
      if (!fs.existsSync(user) || !fs.statSync(user).isFile()) {
        errors.push(`${at}: \`used-by: ${value[1]}\` does not exist.`);
        return;
      }
      // Whole word: `visualQa` is inside `visualQaVerdict`, a different module.
      if (!new RegExp(`(?<![\\w$-])${escapeRegExp(name)}(?![\\w$-])`).test(fs.readFileSync(user, "utf8"))) {
        errors.push(`${at}: \`used-by: ${value[1]}\` never names ${name}.`);
        return;
      }
    }
    markers.push({ kind, value: value[1], at });
  });
  return markers;
}

function check({ repoRoot, diffScope }) {
  const errors = [];
  const sourceRoot = tsSourceRoot(repoRoot);
  const files = listSources(repoRoot, sourceRoot, diffScope);
  if (files.length === 0) return errors;
  const ts = requireClientModule("typescript");
  const starts = entries(sourceRoot);
  if (starts.length === 0) {
    errors.push(
      `${path.relative(repoRoot, path.dirname(sourceRoot))}/index.html names no module entry, so nothing can be judged reachable.`
    );
    return errors;
  }
  const { edges, contents } = buildGraph(ts, files, sourceRoot, repoRoot, errors);
  const inApp = reach(edges, starts);

  const covered = [];
  for (const file of files) {
    if (isTestSupport(file, sourceRoot)) continue;
    for (const marker of markersIn(contents.get(file), file, repoRoot, errors)) {
      if (inApp.has(file) && marker.kind === "kept-for") {
        errors.push(
          `${marker.at}: the app reaches this module now, so \`kept-for: ${marker.value}\` is out of date. Remove it.`
        );
      } else {
        covered.push(file);
      }
    }
    if (!inApp.has(file) && augmentsTypes(ts, file, contents.get(file))) covered.push(file);
  }

  const live = reach(edges, [...inApp, ...covered]);
  for (const file of files) {
    if (live.has(file) || isTestSupport(file, sourceRoot)) continue;
    errors.push(
      `${path.relative(repoRoot, file)}: nothing in the app imports this module. Wire it in, delete it ` +
        "(and its tests), or link it to the open ticket that needs it: " +
        "`// kept-for: <ticket-external-id> - <reason>`."
    );
  }
  return errors;
}

const errors = check(parseArgv(process.argv.slice(2)));
if (errors.length > 0) {
  console.error(`ts-unreachable: ${errors.length} finding(s)\n`);
  for (const e of errors) console.error(`  ${e}`);
  process.exit(1);
}
