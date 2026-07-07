import { render, screen } from "@testing-library/react";

import { MarkdownContent } from "../MarkdownContent";

describe("MarkdownContent", () => {
  it("renders GFM markdown tables", () => {
    const table = [
      "| Situation | Tool |",
      "|-----------|------|",
      "| Read ticket | `loregarden_get_ticket` |",
    ].join("\n");

    render(<MarkdownContent content={table} normalize={false} />);

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Situation" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Tool" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Read ticket" })).toBeInTheDocument();
    expect(screen.getByText("loregarden_get_ticket").tagName).toBe("CODE");
  });
});
