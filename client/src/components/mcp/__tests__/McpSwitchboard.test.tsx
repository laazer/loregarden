import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "fs";
import { join } from "path";

import { McpSwitchboard } from "../McpSwitchboard";

const CSS = readFileSync(join(__dirname, "..", "..", "..", "pages", "McpGatewayPage.css"), "utf8");

const agent = { key: "a", label: "Acceptance Criteria Gatekeeper", initials: "AC", connected: true };
const server = { key: "s", name: "loregarden", connected: true, healthy: true, builtIn: true };

/** `left` from the first (full-width) rule for a node, in percent. */
const column = (node: string) =>
  new RegExp(`\\.mcp-node--${node} \\{\\s*left: (\\d+)%`).exec(CSS)?.[1];

it("starts each wire where the stylesheet puts its column", () => {
  const { container } = render(
    <McpSwitchboard agents={[agent]} servers={[server]} selectedServer={null} onSelectServer={() => {}} />,
  );

  const [toAgent, toServer] = Array.from(container.querySelectorAll("line"));
  expect(toAgent.getAttribute("x1")).toBe(column("agent"));
  expect(toServer.getAttribute("x2")).toBe(column("server"));
});

it("keeps a cut name whole on its node, and selects a server by its name", async () => {
  const onSelectServer = jest.fn();
  render(
    <McpSwitchboard agents={[agent]} servers={[server]} selectedServer={null} onSelectServer={onSelectServer} />,
  );

  expect(screen.getByTitle("Acceptance Criteria Gatekeeper")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "loregarden" }));
  expect(onSelectServer).toHaveBeenCalledWith("s");
});
