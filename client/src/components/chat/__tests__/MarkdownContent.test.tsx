import { render, screen } from "@testing-library/react";

import { MarkdownContent, MarkdownPre } from "../MarkdownContent";

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

  // The jest mock of react-markdown ignores `components`, so the renderer is
  // driven with the syntax-tree node react-markdown hands it.
  const fence = (lang: string, value: string) => ({
    type: "element" as const,
    tagName: "pre",
    properties: {},
    children: [
      {
        type: "element" as const,
        tagName: "code",
        properties: { className: [`language-${lang}`] },
        children: [{ type: "text" as const, value }],
      },
    ],
  });

  it("folds a card fence the server could not parse instead of dumping its JSON", () => {
    const raw = '{"primitive":"qa","question":"Which?","options":[]}';
    const { container } = render(
      <MarkdownPre node={fence("loregarden", raw)}>
        <code>{raw}</code>
      </MarkdownPre>,
    );

    const folded = container.querySelector("details");
    expect(folded).not.toBeNull();
    expect(folded).not.toHaveAttribute("open");
    expect(screen.getByText(/A qa card couldn.t be shown/).tagName).toBe("SUMMARY");
    expect(folded).toHaveTextContent('"options"');
  });

  it("leaves every other code block a plain code block", () => {
    const { container } = render(
      <MarkdownPre node={fence("ts", "const x = 1;")}>
        <code>const x = 1;</code>
      </MarkdownPre>,
    );

    expect(container.querySelector("details")).toBeNull();
    expect(container.querySelector("pre")).toHaveTextContent("const x = 1;");
  });
});
