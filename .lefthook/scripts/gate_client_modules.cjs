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
 * This exits rather than throws on purpose: each gate's `parseFile` catches
 * everything `parse` raises and treats it as a syntax error to skip, so a thrown
 * error from a lazy load would become a silent pass over every file.
 *
 * Loads lazily and caches, so a run with nothing to parse never pays for (or
 * needs) the parser.
 */

const path = require("path");
const { createRequire } = require("module");

/** `sysexits.h` EX_UNAVAILABLE; `gate_runner.GATE_EX_UNAVAILABLE` agrees. */
const EX_UNAVAILABLE = 69;

const clientRoot = path.resolve(__dirname, "../../client");
const requireFromClient = createRequire(path.join(clientRoot, "package.json"));
const loaded = new Map();

function requireClientModule(name) {
  if (!loaded.has(name)) {
    try {
      loaded.set(name, requireFromClient(name));
    } catch (err) {
      if (err.code !== "MODULE_NOT_FOUND") throw err;
      const gate = path.basename(process.argv[1] ?? "gate");
      console.error(
        `${gate}: cannot load ${name} from ${clientRoot}/node_modules ` +
          `(${err.message.split("\n")[0]}).\n` +
          "This gate examined nothing — it is unavailable, not passing or failing.\n" +
          `Install the client toolchain: cd ${clientRoot} && npm ci`
      );
      process.exit(EX_UNAVAILABLE);
    }
  }
  return loaded.get(name);
}

/** The TypeScript parser every TS gate uses. */
function parse(content, options) {
  return requireClientModule("@typescript-eslint/typescript-estree").parse(content, options);
}

module.exports = { EX_UNAVAILABLE, requireClientModule, parse };
