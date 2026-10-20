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
 * Loads lazily and caches, so a run with nothing to parse never pays for (or
 * needs) the parser.
 */

const fs = require("fs");
const path = require("path");
const { createRequire } = require("module");

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
 * Refuse a Node outside the package's declared `engines.node`. The range is
 * matched with `semver` as the package itself resolves it (typescript-estree
 * depends on it); a package that declares no range is not checked.
 */
function requireSupportedNode(name) {
  const manifestPath = path.join(clientRoot, "node_modules", name, "package.json");
  if (!fs.existsSync(manifestPath)) return; // the load below reports MODULE_NOT_FOUND
  const range = JSON.parse(fs.readFileSync(manifestPath, "utf8")).engines?.node;
  if (!range) return;
  let semver;
  try {
    semver = createRequire(manifestPath)("semver");
  } catch (err) {
    unavailable(
      `cannot check Node ${process.version} against ${name}'s engines "${range}": ` +
        `semver does not load (${err.message.split("\n")[0]}).`,
      `Reinstall the client toolchain: cd ${clientRoot} && npm ci`
    );
  }
  if (!semver.satisfies(process.versions.node, range)) {
    unavailable(
      `${name} needs Node ${range}; this is Node ${process.version} (${process.execPath}).`,
      "Put Node 20 first on PATH (`nvm use 20`); CI pins Node 20."
    );
  }
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

/** The TypeScript parser every TS gate uses. */
function parse(content, options) {
  return requireClientModule("@typescript-eslint/typescript-estree").parse(content, options);
}

module.exports = { EX_UNAVAILABLE, requireClientModule, parse };
