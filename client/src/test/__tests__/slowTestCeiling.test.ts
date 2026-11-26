import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

interface CeilingModule {
  overCeilingError: (args: {
    id: string;
    durationMs: number | undefined;
    exempt: boolean;
    factor: number;
    ceilingMs?: number;
  }) => Error | null;
}

// require: a CommonJS jest environment, not an app module with types.
const ceiling = require("../slowTestEnvironment.cjs") as CeilingModule;

const CLIENT = path.resolve(__dirname, "../../..");
const ENVIRONMENT = path.join(CLIENT, "src/test/slowTestEnvironment.cjs");
const SLOW_TEST = path.join(CLIENT, "src/test/slowTest.ts");

describe("overCeilingError", () => {
  it("names the measured time and the ceiling when an unexempt test is over", () => {
    const error = ceiling.overCeilingError({
      id: "src/a.test.ts::slow",
      durationMs: 3500,
      exempt: false,
      factor: 1,
      ceilingMs: 3000,
    });
    expect(error?.message).toContain("src/a.test.ts::slow took 3500ms");
    expect(error?.message).toContain("ceiling of 3000ms");
  });

  it("passes an exempt test however long it took", () => {
    expect(
      ceiling.overCeilingError({ id: "x", durationMs: 99_000, exempt: true, factor: 1 }),
    ).toBeNull();
  });

  it("scales the ceiling by the load factor", () => {
    const args = { id: "x", durationMs: 5000, exempt: false, ceilingMs: 3000 };
    expect(ceiling.overCeilingError({ ...args, factor: 2 })).toBeNull();
    expect(ceiling.overCeilingError({ ...args, factor: 1.5 })?.message).toContain(
      "load factor 1.50",
    );
  });
});

interface JestAssertion {
  title: string;
  status: string;
  failureMessages: string[];
}

interface JestJson {
  testResults: { assertionResults: JestAssertion[] }[];
}

const FIXTURE_TEST = `
import { slowTest } from ${JSON.stringify(SLOW_TEST)};

const wait = () => new Promise((resolve) => setTimeout(resolve, 300));

test("fast", () => {});
test("slow and unmarked", wait);
slowTest("it waits on purpose", "slow and marked", wait);
slowTest("   ", "marked with a blank reason", () => {});
test("slow and inherited", wait);
`;

/** A second file, inherited whole: an entry with no `::` exempts every test in it. */
const FIXTURE_INHERITED_FILE = `
test("slow in an inherited file", () => new Promise((resolve) => setTimeout(resolve, 300)));
`;

/** GitHub Actions sets CI=true; the ceiling fails tests only there. */
function fixtureEnv(ci: boolean): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env };
  delete env.CI;
  if (ci) env.CI = "true";
  return env;
}

/** Runs a real jest over a fixture, with the ceiling pinned to 50ms at load factor 1. */
interface FixtureRun {
  results: Map<string, JestAssertion>;
  stderr: string;
}

function runFixture(ci: boolean): FixtureRun {
  const root = mkdtempSync(path.join(tmpdir(), "slow-test-ceiling-"));
  try {
    writeFileSync(
      path.join(root, "environment.cjs"),
      `const Base = require(${JSON.stringify(ENVIRONMENT)});
module.exports = class extends Base {
  constructor(config, context) {
    super(config, context);
    this.ceilingMs = 50;
    this.loadFactor = () => 1;
    this.inherited = new Set(["fixture.test.ts::slow and inherited", "inheritedFile.test.ts"]);
  }
};
`,
    );
    writeFileSync(path.join(root, "fixture.test.ts"), FIXTURE_TEST);
    writeFileSync(path.join(root, "inheritedFile.test.ts"), FIXTURE_INHERITED_FILE);
    // The project's own transform, so the fixture compiles slowTest.ts as the suite does.
    // require: jest.config.cjs is CommonJS.
    const config = require(path.join(CLIENT, "jest.config.cjs")) as {
      transform: Record<string, [string, unknown]>;
    };
    // Resolved from here: the fixture's rootDir has no node_modules to find it in.
    const transform = Object.fromEntries(
      Object.entries(config.transform).map(([pattern, [name, options]]) => [
        pattern,
        [require.resolve(name), options],
      ]),
    );
    writeFileSync(
      path.join(root, "jest.config.json"),
      JSON.stringify({
        rootDir: root,
        roots: [root],
        testEnvironment: path.join(root, "environment.cjs"),
        testMatch: ["**/fixture.test.ts", "**/inheritedFile.test.ts"],
        transform,
      }),
    );
    const out = path.join(root, "result.json");
    const run = spawnSync(
      process.execPath,
      [
        path.join(CLIENT, "node_modules/jest/bin/jest.js"),
        "--config",
        path.join(root, "jest.config.json"),
        "--json",
        "--outputFile",
        out,
        "--runInBand",
      ],
      { cwd: CLIENT, encoding: "utf8", timeout: 60_000, env: fixtureEnv(ci) },
    );
    const raw = (() => {
      try {
        return JSON.parse(readFileSync(out, "utf8")) as JestJson;
      } catch (error) {
        throw new Error(`the fixture run wrote no result (${String(error)}):\n${run.stderr}`);
      }
    })();
    return {
      results: new Map(raw.testResults.flatMap((file) => file.assertionResults).map((a) => [a.title, a])),
      stderr: run.stderr,
    };
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

describe("the jest environment, in a CI run", () => {
  let results: Map<string, JestAssertion>;

  beforeAll(() => {
    results = runFixture(true).results;
  }, 60_000);

  it("fails an unmarked test over the ceiling, with its time and the ceiling", () => {
    const failed = results.get("slow and unmarked");
    expect(failed?.status).toBe("failed");
    expect(failed?.failureMessages.join("\n")).toMatch(
      /fixture\.test\.ts::slow and unmarked took \d+ms .* ceiling of 50ms/,
    );
  });

  it("passes a fast test, a marked one and an inherited one", () => {
    expect(results.get("fast")?.status).toBe("passed");
    expect(results.get("slow and marked")?.status).toBe("passed");
    expect(results.get("slow and inherited")?.status).toBe("passed");
  });

  it("passes every test in a file the ratchet lists whole", () => {
    expect(results.get("slow in an inherited file")?.status).toBe("passed");
  });

  it("fails a test marked with a blank reason", () => {
    const blank = results.get("marked with a blank reason");
    expect(blank?.status).toBe("failed");
    expect(blank?.failureMessages.join("\n")).toContain("needs a non-empty reason");
  });
});

describe("the jest environment, off CI", () => {
  let run: FixtureRun;

  beforeAll(() => {
    run = runFixture(false);
  }, 60_000);

  it("reports an unmarked overrun on stderr and fails nothing for it", () => {
    expect(run.results.get("slow and unmarked")?.status).toBe("passed");
    expect(run.stderr).toMatch(/slow test \(reported; CI fails this\): fixture\.test\.ts::slow and unmarked took/);
  });

  it("still fails a blank reason", () => {
    expect(run.results.get("marked with a blank reason")?.status).toBe("failed");
  });
});
