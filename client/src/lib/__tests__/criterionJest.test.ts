import { join } from "node:path";

// Plain CommonJS: the server's criteria check runs it with `node`, not jest.
const { parseTestId, outcomes } = require("../../../scripts/criterion-jest.cjs") as {
  parseTestId: (id: string) => { file: string; name: string } | null;
  outcomes: (
    tests: { file: string; name: string }[],
    report: unknown,
  ) => { test: { file: string; name: string }; status: string }[];
};

const CLIENT_ROOT = join(__dirname, "..", "..", "..");
const FILE = "src/lib/__tests__/example.test.ts";

function report(...assertions: { title: string; fullName?: string; status: string }[]) {
  return {
    testResults: [
      {
        name: join(CLIENT_ROOT, FILE),
        assertionResults: assertions.map((a) => ({ fullName: a.title, ...a })),
      },
    ],
  };
}

describe("parseTestId", () => {
  it("reads a criterion's client test id relative to the client root", () => {
    expect(parseTestId(`client/${FILE}::"keeps the name"`)).toEqual({
      file: FILE,
      name: "keeps the name",
    });
  });

  it("refuses an id with no test name", () => {
    expect(parseTestId(`client/${FILE}`)).toBeNull();
    expect(parseTestId(`client/${FILE}::""`)).toBeNull();
  });
});

describe("outcomes", () => {
  const named = { file: FILE, name: "keeps the name" };

  it("passes a named test the report shows passed", () => {
    expect(outcomes([named], report({ title: "keeps the name", status: "passed" }))).toEqual([
      { test: named, status: "passed" },
    ]);
  });

  it("matches a test by its full name under a describe", () => {
    const nested = { file: FILE, name: "Panel keeps the name" };
    const result = outcomes(
      [nested],
      report({ title: "keeps the name", fullName: "Panel keeps the name", status: "passed" }),
    );
    expect(result[0].status).toBe("passed");
  });

  it("reports a named test jest skipped as not passed, though jest exits 0", () => {
    expect(outcomes([named], report({ title: "keeps the name", status: "pending" }))[0].status).toBe(
      "pending",
    );
  });

  it("reports a named test missing from the report", () => {
    expect(outcomes([named], report({ title: "another test", status: "passed" }))[0].status).toBe(
      "missing",
    );
    expect(outcomes([named], { testResults: [] })[0].status).toBe("missing");
  });

  it("reports a failure when any test of that name failed", () => {
    const result = outcomes(
      [named],
      report(
        { title: "keeps the name", status: "passed" },
        { title: "keeps the name", status: "failed" },
      ),
    );
    expect(result[0].status).toBe("failed");
  });
});
