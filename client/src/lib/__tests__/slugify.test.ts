import { slugify } from "../slugify";

describe("slugify", () => {
  it("lowercases and hyphenates", () => {
    expect(slugify("Blobert Game")).toBe("blobert-game");
  });

  it("strips leading and trailing separators", () => {
    expect(slugify("  --My Project--  ")).toBe("my-project");
  });

  it("returns empty for punctuation-only input", () => {
    expect(slugify("!!!")).toBe("");
  });
});
