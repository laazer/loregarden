import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { MemoryRouter } from "react-router-dom";

import type { AgentRunStats, StudioAgent } from "../../../api/types";
import { AgentIndex } from "../AgentIndex";

function agent(slug: string, extra: Partial<StudioAgent> = {}): StudioAgent {
  return { slug, name: slug, description: "", built_in: true, ...extra } as StudioAgent;
}

const AGENTS = [agent("planner"), agent("repair"), agent("blog_post"), agent("my-agent", { built_in: false })];
const STATS: AgentRunStats[] = [
  { agent_id: "planner", runs: 660, failed: 32, last_run_at: "2026-10-28T12:00:00" },
  { agent_id: "repair", runs: 149, failed: 96, last_run_at: "2026-10-28T12:00:00" },
  { agent_id: "my-agent", runs: 3, failed: 0, last_run_at: "2026-10-20T12:00:00" },
];

function ok<T>(data: T) {
  return { data, isPending: false, isError: false, error: null, refetch: jest.fn() };
}

function renderIndex(overrides: Partial<ComponentProps<typeof AgentIndex>> = {}) {
  const props: ComponentProps<typeof AgentIndex> = {
    agents: ok(AGENTS),
    stats: ok(STATS),
    onNewAgent: jest.fn(),
    newAgentOpen: false,
    ...overrides,
  };
  render(
    <MemoryRouter>
      <AgentIndex {...props} />
    </MemoryRouter>,
  );
  return props;
}

const bodyRows = () => within(screen.getByRole("table")).getAllByRole("row").slice(1);
const firstCells = () => bodyRows().map((row) => within(row).getByRole("rowheader").querySelector("a")?.textContent);

describe("AgentIndex", () => {
  it("opens worst failure rate first, each name a link to its editor", () => {
    renderIndex();
    expect(firstCells()).toEqual(["repair", "planner", "my-agent", "blog_post"]);
    expect(screen.getByRole("columnheader", { name: /Failure rate/ })).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("link", { name: "repair" })).toHaveAttribute("href", "/studio/agents/repair");
    expect(screen.getByText("64%")).toBeInTheDocument();
  });

  it("re-sorts from a header, and says which way", async () => {
    renderIndex();
    await userEvent.click(screen.getByRole("button", { name: /^Runs/ }));
    expect(screen.getByRole("columnheader", { name: /^Runs/ })).toHaveAttribute("aria-sort", "descending");
    expect(firstCells()[0]).toBe("planner");
    await userEvent.click(screen.getByRole("button", { name: /^Runs/ }));
    expect(screen.getByRole("columnheader", { name: /^Runs/ })).toHaveAttribute("aria-sort", "ascending");
  });

  it("names the agents that have never run", () => {
    renderIndex();
    expect(screen.getByText(/never run: blog_post\./)).toBeInTheDocument();
  });

  it("filters, and offers the way back when nothing matches", async () => {
    renderIndex();
    await userEvent.type(screen.getByRole("searchbox", { name: "Filter agents" }), "zzz");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show every agent" }));
    expect(bodyRows()).toHaveLength(4);
  });

  it("opens New agent… from its button", async () => {
    const props = renderIndex();
    await userEvent.click(screen.getByRole("button", { name: "New agent…" }));
    expect(props.onNewAgent).toHaveBeenCalledTimes(1);
  });

  it("still lists the agents when only the run counts fail, and says the counts are missing", async () => {
    const stats = { data: undefined, isPending: false, isError: true, error: new Error("boom"), refetch: jest.fn() };
    renderIndex({ stats });
    expect(screen.getByRole("alert")).toHaveTextContent("Run counts did not load");
    expect(bodyRows()).toHaveLength(4);
    // A missing count is a dash, never a plausible zero or "never".
    expect(screen.queryByText("never")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(stats.refetch).toHaveBeenCalled();
  });

  it("keeps a failed agent list apart from an empty one", () => {
    renderIndex({
      agents: { data: undefined, isPending: false, isError: true, error: new Error("down"), refetch: jest.fn() },
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load the agents");
  });

  it("says what to do when there are no agents at all", () => {
    renderIndex({ agents: ok([]), stats: ok([]) });
    expect(screen.getByText(/No agents yet/)).toBeInTheDocument();
  });

  it("shows a skeleton while the agents load", () => {
    renderIndex({ agents: { data: undefined, isPending: true, isError: false, error: null, refetch: jest.fn() } });
    expect(screen.getByTestId("pane-skeleton")).toBeInTheDocument();
  });
});
