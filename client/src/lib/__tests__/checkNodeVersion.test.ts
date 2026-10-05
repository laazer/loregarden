import { readFileSync } from "node:fs";
import { join } from "node:path";

// The checker is plain CommonJS so it runs under the too-old Node it rejects.
const { satisfies, readRequiredRange } = require("../../../scripts/check-node-version.cjs") as {
  satisfies: (version: string, range: string) => boolean;
  readRequiredRange: (packageJsonPath: string) => string;
};

const CLIENT_ROOT = join(__dirname, "..", "..", "..");
const RANGE = "^20.19.0 || ^22.13.0 || >=24.0.0";

describe("satisfies", () => {
  it.each(["v20.19.0", "v20.20.2", "v22.13.0", "v22.21.0", "v24.0.0", "v25.1.3"])(
    "accepts supported %s",
    (version) => {
      expect(satisfies(version, RANGE)).toBe(true);
    },
  );

  it.each(["v14.21.3", "v18.20.8", "v20.18.3", "v21.7.0", "v22.5.1", "v22.12.0", "v23.11.0"])(
    "rejects unsupported %s",
    (version) => {
      expect(satisfies(version, RANGE)).toBe(false);
    },
  );

  it("orders a prerelease below its release", () => {
    expect(satisfies("v22.13.0-rc.1", RANGE)).toBe(false);
    expect(satisfies("v22.13.1-rc.1", RANGE)).toBe(true);
    expect(satisfies("v24.0.0-nightly20250101abcdef", ">=24.0.0")).toBe(false);
    expect(satisfies("v25.0.0-nightly20250101abcdef", ">=24.0.0")).toBe(true);
    expect(satisfies("v24.0.0-rc.10", ">=24.0.0-rc.9")).toBe(true);
  });

  it("reads partial, tilde and bare comparators", () => {
    expect(satisfies("v12.0.0", ">=12")).toBe(true);
    expect(satisfies("v20.3.0", "20")).toBe(true);
    expect(satisfies("v21.0.0", "20")).toBe(false);
    expect(satisfies("v20.19.9", "~20.19.0")).toBe(true);
    expect(satisfies("v20.20.0", "~20.19.0")).toBe(false);
    expect(satisfies("v18.0.0", ">=16 <20")).toBe(true);
    expect(satisfies("v20.0.0", ">=16 <20")).toBe(false);
  });

  it("refuses to guess at what it cannot read", () => {
    expect(() => satisfies("v20.x", RANGE)).toThrow(/unreadable Node version/);
    expect(() => satisfies("", RANGE)).toThrow(/unreadable Node version/);
    expect(() => satisfies("v20.19.0", "node20")).toThrow(/unreadable comparator/);
    expect(() => satisfies("v20.19.0", "^20 ||")).toThrow(/unreadable range/);
  });
});

describe("client/package.json engines.node", () => {
  const range = readRequiredRange(join(CLIENT_ROOT, "package.json"));

  it("is a range the checker can read", () => {
    expect(() => satisfies("v20.19.0", range)).not.toThrow();
  });

  it("is met by the line .nvmrc selects", () => {
    const nvmrc = readFileSync(join(CLIENT_ROOT, "..", ".nvmrc"), "utf8").trim();
    expect(satisfies(`v${nvmrc}.99.0`, range)).toBe(true);
  });
});
