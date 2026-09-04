import { render, screen } from "@testing-library/react";

import { LogLineRow } from "../LogLineRow";

// Reasoning and answer share the feed. The row is where they stop looking
// alike: the tag chip and the row modifier both carry the variant, so a THINK
// line can be styled as commentary rather than as the agent's output.
it("marks a reasoning line with the think variant", () => {
  render(<LogLineRow line={{ time: "10:00:00", tag: "THINK", text: "weighing options" }} />);

  const row = screen.getByText("weighing options").closest(".log-line");
  expect(row).toHaveClass("log-line--think");
  expect(screen.getByText("THINK")).toHaveClass("log-line__tag--think");
});

it("leaves output on the default variant", () => {
  render(<LogLineRow line={{ time: "10:00:00", tag: "OUT", text: "3 passed" }} />);

  const row = screen.getByText("3 passed").closest(".log-line");
  expect(row).toHaveClass("log-line--info");
  expect(row).not.toHaveClass("log-line--think");
});

it("pretty-prints a JSON body and keeps the command headline above it", () => {
  render(
    <LogLineRow
      line={{ time: "10:00:00", tag: "TOOL", text: '$ cat a.json · completed\n{"a":1,"b":2}' }}
    />,
  );

  expect(screen.getByText("$ cat a.json · completed")).toHaveClass("log-line__headline");
  const body = document.querySelector(".log-line__json");
  expect(body).not.toBeNull();
  expect(body?.textContent).toBe('{\n  "a": 1,\n  "b": 2\n}');
});

it("renders a markdown body instead of showing its marks", () => {
  render(
    <LogLineRow line={{ time: "10:00:00", tag: "OUT", text: "## Findings\n\n- one\n- two" }} />,
  );

  expect(document.querySelector(".log-line__md")).not.toBeNull();
});

it("leaves an unparseable bracketed body as plain text", () => {
  const repr = "{'role': 'user', 'content': []}";
  render(<LogLineRow line={{ time: "10:00:00", tag: "OUT", text: repr }} />);

  expect(screen.getByText(repr)).toBeInTheDocument();
  expect(document.querySelector(".log-line__json")).toBeNull();
});
