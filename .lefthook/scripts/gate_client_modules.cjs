/**
 * Load a package from loregarden's `client/node_modules`, or report the gate as
 * unable to run.
 *
 * The TypeScript gates parse with `client/`'s own toolchain. Where it is not
 * installed — any fresh checkout or worktree before `npm ci` — node's
 * MODULE_NOT_FOUND exits 1, exactly like a gate that found violations, and the
 * orchestration runner sends an agent to fix a violation that does not exist.
 * Exit `EX_UNAVAILABLE` (69) instead, which `gate_runner` maps to "could not
 * run" — the Node half of `gate_python_guard.py`.
 *
 * The same goes for a Node too old for the toolchain. Under Node 14,
 * typescript-estree fails to load (`??=` is a SyntaxError there), and every
 * gate reported "passed" over files it never parsed. So a package's own
 * `engines.node` is checked before it loads, and any other load failure is
 * unavailable too.
 *
 * This exits rather than throws on purpose: each gate's `parseFile` catches
 * everything `parse` raises and treats it as a syntax error to skip, so a thrown
 * error from a lazy load would become a silent pass over every file.
 *
 * Loads lazily and caches, so a run with nothing to parse never needs the
 * parser. A gate may `preloadParser()` to overlap the load with other work; a
 * preload that fails is silent, and the lazy load reports it.
 */

const fs = require("fs");
const path = require("path");
const nodeModule = require("module");
const { createRequire } = nodeModule;

// Every gate launch otherwise recompiles typescript.js (~9 MB) from source,
// about a third of a launch's CPU, and a commit or test run launches dozens.
// Node 22.1+ keeps V8's compiled code under os.tmpdir(), keyed by content, so a
// changed file is recompiled. Older Nodes lack it and compile as before; a
// cache that cannot be opened is the same as none (NODE_DISABLE_COMPILE_CACHE
// turns it off).
nodeModule.enableCompileCache?.();

/** `sysexits.h` EX_UNAVAILABLE; `gate_runner.GATE_EX_UNAVAILABLE` agrees. */
const EX_UNAVAILABLE = 69;

const clientRoot = path.resolve(__dirname, "../../client");
const requireFromClient = createRequire(path.join(clientRoot, "package.json"));
const loaded = new Map();

function unavailable(reason, fix) {
  const gate = path.basename(process.argv[1] ?? "gate");
  console.error(
    `${gate}: ${reason}\n` +
      "This gate examined nothing — it is unavailable, not passing or failing.\n" +
      fix
  );
  process.exit(EX_UNAVAILABLE);
}

/**
 * Why `name` cannot load under this Node, as `{ reason, fix }`, or null when it
 * can. The range is matched with `semver` as the package itself resolves it
 * (typescript-estree depends on it); a package that declares no range is not
 * checked. Never exits: `requireSupportedNode` turns an answer into 69, and
 * `preloadClientModule` turns one into "do not load".
 */
function nodeSupportProblem(name) {
  const manifestPath = path.join(clientRoot, "node_modules", name, "package.json");
  if (!fs.existsSync(manifestPath)) return null; // the load reports MODULE_NOT_FOUND
  const range = JSON.parse(fs.readFileSync(manifestPath, "utf8")).engines?.node;
  if (!range) return null;
  let semver;
  try {
    semver = createRequire(manifestPath)("semver");
  } catch (err) {
    return {
      reason:
        `cannot check Node ${process.version} against ${name}'s engines "${range}": ` +
        `semver does not load (${err.message.split("\n")[0]}).`,
      fix: `Reinstall the client toolchain: cd ${clientRoot} && npm ci`,
    };
  }
  if (!semver.satisfies(process.versions.node, range)) {
    return {
      reason: `${name} needs Node ${range}; this is Node ${process.version} (${process.execPath}).`,
      fix: "Put Node 20 first on PATH (`nvm use 20`); CI pins Node 20.",
    };
  }
  return null;
}

/** Refuse a Node outside the package's declared `engines.node`. */
function requireSupportedNode(name) {
  const problem = nodeSupportProblem(name);
  if (problem !== null) unavailable(problem.reason, problem.fix);
}

function requireClientModule(name) {
  if (!loaded.has(name)) {
    requireSupportedNode(name);
    try {
      loaded.set(name, requireFromClient(name));
    } catch (err) {
      if (err.code === "MODULE_NOT_FOUND") {
        unavailable(
          `cannot load ${name} from ${clientRoot}/node_modules ` +
            `(${err.message.split("\n")[0]}).`,
          `Install the client toolchain: cd ${clientRoot} && npm ci`
        );
      }
      unavailable(
        `${name} failed to load under Node ${process.version} ` +
          `(${err.name}: ${err.message.split("\n")[0]}).`,
        `Use Node 20 (\`nvm use 20\`), then reinstall: cd ${clientRoot} && npm ci`
      );
    }
  }
  return loaded.get(name);
}

/**
 * Load `name` now if it will load, and do nothing if it will not.
 *
 * For a gate that has other work to wait on (a subprocess) and would rather
 * spend that wait loading than pay for the load afterwards. It is speculative,
 * so it never exits: a run that turns out to have nothing to parse must still
 * need no toolchain, and a Node outside the package's engines must not even try
 * (that is how Node 14 read every file as clean). Whatever stops it here stops
 * `requireClientModule` again at the first parse, which reports it as 69.
 */
function preloadClientModule(name) {
  if (loaded.has(name) || nodeSupportProblem(name) !== null) return;
  try {
    loaded.set(name, requireFromClient(name));
  } catch {
    // silent-ok: the preload is speculative; node keeps no failed module in its cache, so requireClientModule retries the load at the first parse and reports the failure as 69 there
  }
}

const PARSER = "@typescript-eslint/typescript-estree";

/** The TypeScript parser every TS gate uses. */
function parse(content, options) {
  return requireClientModule(PARSER).parse(content, options);
}

/** `preloadClientModule` for the parser `parse` uses. */
function preloadParser() {
  preloadClientModule(PARSER);
}

module.exports = { EX_UNAVAILABLE, requireClientModule, parse, preloadParser };
