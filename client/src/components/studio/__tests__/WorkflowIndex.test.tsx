import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { MemoryRouter } from "react-router-dom";

import type { StudioWorkflow, WorkflowUsageStats } from "../../../api/types";
import { WorkflowIndex } from "../WorkflowIndex";

function workflow(slug: string, stages = 3): StudioWorkflow {
  return {
    slug,
    name: slug,
    description: "",
    stages: Array.from({ length: stages }, (_, i) => ({ key: `s${i}` })),
    transitions: [],
    published_template_id: `tpl-${slug}`,
    published_template_slug: slug,
    built_in: true,
  } as unknown as StudioWorkflow;
}

const WORKFLOWS = ["v3", "tdd", "blobert", "survey", "unused-a", "unused-b"].map((slug) => workflow(slug));
const USAGE: WorkflowUsageStats[] = [
  { template_slug: "v3", tickets: 1579, open: 1183, last_used_at: "2026-10-28T12:00:00" },
  { template_slug: "tdd", tickets: 256, open: 216, last_used_at: "2026-10-24T12:00:00" },
  { template_slug: "blobert", tickets: 162, open: 123, last_used_at: "2026-10-24T12:00:00" },
  { template_slug: "survey", tickets: 62, open: 1, last_used_at: "2026-10-24T12:00:00" },
  { template_slug: "unused-a", tickets: 0, open: 0, last_used_at: null },
  { template_slug: "unused-b", tickets: 0, open: 0, last_used_at: null },
];

function ok<T>(data: T) {
  return { data, isPending: false, isError: false, error: null, refetch: jest.fn() };
}

function renderIndex(overrides: Partial<ComponentProps<typeof WorkflowIndex>> = {}) {
  const props: ComponentProps<typeof WorkflowIndex> = {
    workflows: ok(WORKFLOWS),
    usage: ok(USAGE),
    onNewWorkflow: jest.fn(),
    newWorkflowOpen: false,
    ...overrides,
  };
  render(
    <MemoryRouter>
      <WorkflowIndex {...props} />
    </MemoryRouter>,
  );
  return props;
}

const names = () =>
  within(screen.getByRole("table"))
    .queryAllByRole("rowheader")
    .map((cell) => cell.querySelector("a")?.textContent);

describe("WorkflowIndex", () => {
  it("leads with the most-used workflows, each a link to its editor", () => {
    renderIndex();
    expect(names()).toEqual(["v3", "tdd", "blobert", "survey"]);
    expect(screen.getByRole("columnheader", { name: /Tickets/ })).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("link", { name: "v3" })).toHaveAttribute("href", "/studio/workflows/v3");
  });

  it("folds the ones no ticket has used, and opens them on request", async () => {
    renderIndex();
    const toggle = screen.getByRole("button", { name: /2 workflows no ticket has used/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(names()).toEqual(["v3", "tdd", "blobert", "survey", "unused-a", "unused-b"]);
  });

  it("folds nothing while filtering, so a search by name finds an unused one", async () => {
    renderIndex();
    await userEvent.type(screen.getByRole("searchbox", { name: "Filter workflows" }), "unused-a");
    expect(names()).toEqual(["unused-a"]);
    expect(screen.queryByRole("button", { name: /no ticket has used/ })).not.toBeInTheDocument();
  });

  it("folds nothing and shows dashes when the ticket counts fail", () => {
    renderIndex({
      usage: { data: undefined, isPending: false, isError: true, error: new Error("boom"), refetch: jest.fn() },
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Ticket counts did not load");
    expect(names()).toHaveLength(6);
    expect(screen.queryByText("never")).not.toBeInTheDocument();
  });

  it("opens New workflow… from its button", async () => {
    const props = renderIndex();
    await userEvent.click(screen.getByRole("button", { name: "New workflow…" }));
    expect(props.onNewWorkflow).toHaveBeenCalledTimes(1);
  });

  it("keeps a failed list apart from an empty one", () => {
    renderIndex({
      workflows: { data: undefined, isPending: false, isError: true, error: new Error("down"), refetch: jest.fn() },
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load the workflows");
  });

  it("says what to do when there are none", () => {
    renderIndex({ workflows: ok([]), usage: ok([]) });
    expect(screen.getByText(/No workflows yet/)).toBeInTheDocument();
  });
});
