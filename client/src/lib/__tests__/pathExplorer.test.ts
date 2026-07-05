import { browseSeed, browseTargetReached, pathsEqual, sanitizeBrowsePath } from "../pathExplorer";

describe("pathExplorer", () => {
  it("sanitizes shell-escaped paths", () => {
    const raw = "/Users/me/Library/Mobile\\ Documents/Project\\ Vault";
    expect(sanitizeBrowsePath(raw)).toBe("/Users/me/Library/Mobile Documents/Project Vault");
  });

  it("rejects sqlite urls as browse seeds", () => {
    expect(browseSeed("sqlite:///data/loregarden.db")).toBe(".");
  });

  it("preserves normal absolute paths", () => {
    const path = "/Users/me/Library/Mobile Documents/iCloud~md~obsidian/Documents";
    expect(browseSeed(path)).toBe(path);
  });

  it("detects when browse data matches the requested seed", () => {
    const seed = "/Users/me/vault/Project Vault";
    expect(
      browseTargetReached(seed, {
        current_path: seed,
        repo_path: seed,
        parent_path: "/Users/me/vault",
      }),
    ).toBe(true);
    expect(pathsEqual("/Users/me/vault/", "/Users/me/vault")).toBe(true);
  });
});
