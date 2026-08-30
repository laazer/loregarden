import { fireEvent, render, screen } from "@testing-library/react";

import { ToolAccessSection } from "../studio/ToolAccessSection";
import type { StudioAgentToolGrants, ToolGrantWarning } from "../../api/client";

const INHERIT: StudioAgentToolGrants = {
  posture: "inherit",
  allowed_tools: [],
  disallowed_tools: [],
  mcp_servers: [],
};

const ALLOWLIST: StudioAgentToolGrants = { ...INHERIT, posture: "allowlist" };

const WARNING: ToolGrantWarning = {
  code: "auto_approved_excluded",
  message: "These tools would otherwise run without asking.",
  tools: ["mcp__loregarden__loregarden_get_ticket"],
};

function renderSection(
  grants: StudioAgentToolGrants,
  warnings: ToolGrantWarning[] = [],
  onChange = jest.fn(),
) {
  render(
    <ToolAccessSection
      grants={grants}
      warnings={warnings}
      servers={["github", "linear"]}
      onChange={onChange}
    />,
  );
  return onChange;
}

describe("ToolAccessSection", () => {
  it("shows every posture so the default is a visible choice", () => {
    renderSection(INHERIT);
    expect(screen.getByText("Inherit")).toBeInTheDocument();
    expect(screen.getByText("Allowlist")).toBeInTheDocument();
    expect(screen.getByText("Unrestricted")).toBeInTheDocument();
  });

  it("renders warnings with the offending tool names", () => {
    renderSection(ALLOWLIST, [WARNING]);
    expect(screen.getByText(WARNING.message)).toBeInTheDocument();
    expect(
      screen.getByText("mcp__loregarden__loregarden_get_ticket"),
    ).toBeInTheDocument();
  });

  it("renders nothing about warnings when the configuration is clean", () => {
    renderSection(ALLOWLIST, []);
    expect(screen.queryByLabelText("Tool access warnings")).toBeNull();
  });

  it("keeps the CLI tool pickers hidden until the allowlist posture is chosen", () => {
    renderSection(INHERIT);
    expect(screen.queryByText("Bash")).toBeNull();
    renderSection(ALLOWLIST);
    expect(screen.getAllByText("Bash").length).toBeGreaterThan(0);
  });

  it("reports a posture change to the parent", () => {
    const onChange = renderSection(INHERIT);
    fireEvent.click(screen.getByRole("radio", { name: /Allowlist/ }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ posture: "allowlist" }),
    );
  });

  it("toggles a server grant", () => {
    const onChange = renderSection(ALLOWLIST);
    fireEvent.click(screen.getByRole("checkbox", { name: "github" }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ mcp_servers: ["github"] }),
    );
  });

  it("says so when no servers are registered, rather than showing an empty list", () => {
    render(
      <ToolAccessSection
        grants={ALLOWLIST}
        warnings={[]}
        servers={[]}
        onChange={jest.fn()}
      />,
    );
    expect(
      screen.getByText("No MCP servers are registered and enabled."),
    ).toBeInTheDocument();
  });
});
