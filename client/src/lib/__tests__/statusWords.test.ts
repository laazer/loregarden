import { statusToneForWord } from "../statusWords";

describe("statusToneForWord", () => {
  it("reads the words Baxter tabulates as the shared tones", () => {
    expect(statusToneForWord("Reading")).toBe("run");
    expect(statusToneForWord("Queued")).toBe("idle");
    // Needs-you is violet, never amber: amber means blocked everywhere else.
    expect(statusToneForWord("Needs permission")).toBe("you");
    expect(statusToneForWord("Blocked")).toBe("wait");
    expect(statusToneForWord("Failed")).toBe("bad");
    expect(statusToneForWord("  merged ")).toBe("done");
  });

  it("matches whole cells only", () => {
    expect(statusToneForWord("Done by Tuesday")).toBeNull();
    expect(statusToneForWord("Pass 2 running · 711 files")).toBeNull();
    expect(statusToneForWord("")).toBeNull();
  });
});
