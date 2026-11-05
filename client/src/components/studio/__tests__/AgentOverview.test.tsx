import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UseQueryResult } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import type { AgentRunRecord, StudioAgent } from "../../../api/types";
import { AgentOverview } from "../AgentOverview";

// Its own query and its own tests; the overview only places it.
jest.mock("../AgentVersionHistory", () => ({ AgentVersionHistory: () => null }));

const AGENT = {
  slug: "repair",
  name: "Repair",
  description: "Fixes what the merge broke.",
  adapter: "claude",
  default_model: "",
  default_skill: "",
  timeout: 1800,
  mcp_enabled: true,
  mcp_tools: ["a", "b"],
  gate_checks: [],
  handoff_checks: [],
  built_in: true,
} as unknown as StudioAgent;

const RECORD: AgentRunRecord = {
  agent_id: "repair",
  stages: [
    {
      stage_key: "integration-merge",
      runs: 120,
      failed: 90,
      failed_by_kind: [
        { kind: "harness", count: 64 },
        { kind: "work", count: 16 },
        { kind: null, count: 10 },
      ],
    },
    { stage_key: "implement", runs: 40, failed: 0, failed_by_kind: [] },
    { stage_key: "verify", runs: 15, failed: 0, failed_by_kind: [] },
  ],
  recent: [
    {
      id: "r1",
      run_code: "RUN-1",
      status: "failed",
      stage_key: "integration-merge",
      failure_kind: "harness",
      created_at: "2026-11-05T10:00:00",
      finished_at: "2026-11-05T10:05:00",
      ticket_external_id: "lg-thing-12",
      ticket_title: "Make the thing",
    },
    {
      id: "r2",
      run_code: "RUN-2",
      status: "succeeded",
      stage_key: "implement",
      failure_kind: null,
      created_at: "2026-11-05T09:00:00",
      finished_at: "2026-11-05T09:05:00",
      ticket_external_id: null,
      ticket_title: null,
    },
  ],
};

function query<T>(over: Partial<UseQueryResult<T>>): UseQueryResult<T> {
  return { data: undefined, isPending: false, isError: false, error: null, refetch: jest.fn(), ...over } as UseQueryResult<T>;
}

function renderOverview(record: UseQueryResult<AgentRunRecord>, onEdit = jest.fn()) {
  render(
    <MemoryRouter>
      <AgentOverview agent={AGENT} record={record} onEdit={onEdit} />
    </MemoryRouter>,
  );
  return onEdit;
}

const stageRows = () =>
  within(screen.getByRole("table"))
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getByRole("rowheader").textContent);

describe("AgentOverview", () => {
  it("shows the stages it fails at, with why, and folds the clean ones", async () => {
    renderOverview(query({ data: RECORD }));

    expect(stageRows()).toEqual(["integration-merge"]);
    const row = within(screen.getByRole("table")).getAllByRole("row")[1];
    expect(row).toHaveTextContent("90 · 75%");
    expect(row).toHaveTextContent("64 harness · 16 work · 10 unclassified");

    const more = screen.getByRole("button", { name: "2 more stages with no failures" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(more);
    expect(stageRows()).toEqual(["integration-merge", "implement", "verify"]);
    expect(screen.getByRole("button", { name: /hide stages/i })).toHaveAttribute("aria-expanded", "true");
  });

  it("links a recent run to its ticket and names a run with none by its code", () => {
    renderOverview(query({ data: RECORD }));

    expect(screen.getByRole("link", { name: "Make the thing" })).toHaveAttribute("href", expect.stringMatching(/^\/tickets\/lg-thing-12\//));
    expect(screen.getByText("RUN-2")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "failed" })).toBeInTheDocument();
  });

  it("says what starts a record when there are no runs", () => {
    renderOverview(query({ data: { agent_id: "repair", stages: [], recent: [] } }));

    expect(screen.getByText(/no finished runs yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("reports a failed load with a retry, not an empty record", async () => {
    const refetch = jest.fn();
    renderOverview(query<AgentRunRecord>({ isError: true, error: new Error("boom"), refetch }));

    expect(screen.getByRole("alert")).toHaveTextContent(/could not load this agent's runs/i);
    expect(screen.queryByText(/no finished runs yet/i)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("sends the edit buttons to their tabs", async () => {
    const onEdit = renderOverview(query({ data: RECORD }));

    await userEvent.click(screen.getByRole("button", { name: "Edit runtime & tools" }));
    expect(onEdit).toHaveBeenCalledWith("runtime");
  });
});
