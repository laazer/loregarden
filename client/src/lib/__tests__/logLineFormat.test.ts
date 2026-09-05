import { prettyJson, splitLogLine } from "../logLineFormat";

describe("prettyJson", () => {
  it("re-indents a JSON body", () => {
    expect(prettyJson('{"a":1,"b":[2,3]}')).toBe('{\n  "a": 1,\n  "b": [\n    2,\n    3\n  ]\n}');
  });

  it("returns null for text that only looks bracketed", () => {
    // A Python repr and a truncated payload both land here; neither is JSON,
    // and neither should be silently repaired into something that parses.
    expect(prettyJson("{'role': 'user', 'content': []}")).toBeNull();
    expect(prettyJson('{"a": 1, "b": [2,')).toBeNull();
    expect(prettyJson("3 passed in 0.4s")).toBeNull();
  });
});

describe("splitLogLine", () => {
  it("splits a TOOL line into its headline and body", () => {
    const parts = splitLogLine({
      time: "10:00:00",
      tag: "TOOL",
      text: '$ cat a.json · completed\n{"a":1}',
    });
    expect(parts.headline).toBe("$ cat a.json · completed");
    expect(parts.format).toBe("json");
    expect(parts.body).toBe('{\n  "a": 1\n}');
  });

  it("keeps a non-TOOL line whole", () => {
    const parts = splitLogLine({ time: "10:00:00", tag: "OUT", text: "first\nsecond" });
    expect(parts.headline).toBe("");
    expect(parts.body).toBe("first\nsecond");
    expect(parts.format).toBe("plain");
  });

  it("detects markdown from two or more marks", () => {
    const md = "## Findings\n\n- one\n- two";
    expect(splitLogLine({ time: "t", tag: "OUT", text: md }).format).toBe("markdown");
  });

  it("does not read a diff or a flag list as markdown", () => {
    const diff = "- removed line\nnothing else here";
    expect(splitLogLine({ time: "t", tag: "OUT", text: diff }).format).toBe("plain");
    const single = "# just one heading and prose below";
    expect(splitLogLine({ time: "t", tag: "OUT", text: single }).format).toBe("plain");
  });
});
