import { logTagVariant } from "../logLineStyle";

describe("logTagVariant", () => {
  it("maps known log tags to style variants", () => {
    expect(logTagVariant("INFO")).toBe("info");
    expect(logTagVariant("CMD")).toBe("info");
    expect(logTagVariant("OUT")).toBe("info");
    expect(logTagVariant("OK")).toBe("ok");
    expect(logTagVariant("RUN")).toBe("run");
    expect(logTagVariant("TOOL")).toBe("run");
    expect(logTagVariant("WARN")).toBe("warn");
    expect(logTagVariant("ERR")).toBe("err");
    expect(logTagVariant("FAIL")).toBe("err");
  });

  it("is case insensitive", () => {
    expect(logTagVariant("run")).toBe("run");
    expect(logTagVariant("Ok")).toBe("ok");
  });

  it("falls back to info for missing tags instead of throwing", () => {
    expect(logTagVariant(undefined)).toBe("info");
    expect(logTagVariant(null)).toBe("info");
    expect(logTagVariant("")).toBe("info");
  });
});
