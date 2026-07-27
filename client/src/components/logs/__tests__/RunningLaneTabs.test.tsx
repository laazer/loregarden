import { fireEvent, render, screen } from "@testing-library/react";

import type { LedgerAttempt } from "../../../api/types";
import { RunningLaneTabs } from "../RunningLaneTabs";

// Requirements 2, 3, 6 — the tab strip itself: one button per running lane,
// ordered as given, with the standard ARIA tab pattern and a label that
// reflects live ledger status. Per-lane log content, selection-gated
// fetching, and drop-out fallback are exercised at the LogsPanel level
// (LogsPanel.test.tsx), since LogsPanel owns which lane is selected and what
// happens when a lane disappears.

function lane(overrides: Partial<LedgerAttempt> = {}): LedgerAttempt {
  return {
    run_id: "r1",
    run_code: "run_a",
    agent_id: "backend_implementer",
    skill_name: "",
    status: "running",
    started_at: null,
    finished_at: null,
    duration_seconds: null,
    ...overrides,
  };
}

it("renders nothing when there are no running lanes", () => {
  const { container } = render(
    <RunningLaneTabs lanes={[]} selectedRunId={null} onSelect={jest.fn()} />,
  );
  expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  expect(container.firstChild).toBeNull();
});

it("renders exactly one tab per running lane, in the given order", () => {
  render(
    <RunningLaneTabs
      lanes={[
        lane({ run_id: "r1", agent_id: "planner", status: "running" }),
        lane({ run_id: "r2", agent_id: "gatekeeper", status: "queued" }),
      ]}
      selectedRunId="r1"
      onSelect={jest.fn()}
    />,
  );

  const tabs = screen.getAllByRole("tab");
  expect(tabs).toHaveLength(2);
  expect(tabs[0]).toHaveTextContent("planner");
  expect(tabs[1]).toHaveTextContent("gatekeeper");
});

it("gives the strip role=tablist and each button role=tab with aria-selected reflecting the active lane", () => {
  render(
    <RunningLaneTabs
      lanes={[lane({ run_id: "r1" }), lane({ run_id: "r2", agent_id: "gatekeeper" })]}
      selectedRunId="r2"
      onSelect={jest.fn()}
    />,
  );

  expect(screen.getByRole("tablist")).toBeInTheDocument();
  const tabs = screen.getAllByRole("tab");
  expect(tabs[0]).toHaveAttribute("aria-selected", "false");
  expect(tabs[1]).toHaveAttribute("aria-selected", "true");
});

it("labels a tab with agent_id, skill_name (when present), and status", () => {
  render(
    <RunningLaneTabs
      lanes={[
        lane({ run_id: "r1", agent_id: "planner", skill_name: "plan-risk", status: "running" }),
        lane({ run_id: "r2", agent_id: "gatekeeper", skill_name: "", status: "queued" }),
      ]}
      selectedRunId="r1"
      onSelect={jest.fn()}
    />,
  );

  const tabs = screen.getAllByRole("tab");
  expect(tabs[0]).toHaveTextContent("planner · plan-risk · running");
  expect(tabs[1]).toHaveTextContent("gatekeeper · queued");
  expect(tabs[1]).not.toHaveTextContent("· ·");
});

it("calls onSelect with the lane's run_id when its tab is clicked", () => {
  const onSelect = jest.fn();
  render(
    <RunningLaneTabs
      lanes={[lane({ run_id: "r1" }), lane({ run_id: "r2", agent_id: "gatekeeper" })]}
      selectedRunId="r1"
      onSelect={onSelect}
    />,
  );

  fireEvent.click(screen.getByRole("tab", { name: /gatekeeper/ }));
  expect(onSelect).toHaveBeenCalledWith("r2");
});

it("does not throw when selectedRunId does not match any lane — every tab reports aria-selected=false", () => {
  render(
    <RunningLaneTabs
      lanes={[lane({ run_id: "r1", agent_id: "planner" }), lane({ run_id: "r2", agent_id: "gatekeeper" })]}
      selectedRunId="stale-run-id-not-in-lanes"
      onSelect={jest.fn()}
    />,
  );

  const tabs = screen.getAllByRole("tab");
  expect(tabs).toHaveLength(2);
  tabs.forEach((tab) => expect(tab).toHaveAttribute("aria-selected", "false"));
});

it("handles selectedRunId of null with a populated lane list — no crash, nothing marked selected", () => {
  render(
    <RunningLaneTabs
      lanes={[lane({ run_id: "r1" })]}
      selectedRunId={null}
      onSelect={jest.fn()}
    />,
  );

  expect(screen.getByRole("tab")).toHaveAttribute("aria-selected", "false");
});

it("renders one tab per lane even under a large number of concurrent parallel lanes (stress)", () => {
  const lanes = Array.from({ length: 40 }, (_, i) => lane({ run_id: `r${i}`, agent_id: `agent_${i}` }));
  render(<RunningLaneTabs lanes={lanes} selectedRunId="r0" onSelect={jest.fn()} />);

  expect(screen.getAllByRole("tab")).toHaveLength(40);
});

it("still renders both tabs when two lanes share the same run_id (malformed ledger data) without throwing", () => {
  render(
    <RunningLaneTabs
      lanes={[
        lane({ run_id: "dup", agent_id: "planner" }),
        lane({ run_id: "dup", agent_id: "gatekeeper" }),
      ]}
      selectedRunId="dup"
      onSelect={jest.fn()}
    />,
  );

  expect(screen.getAllByRole("tab")).toHaveLength(2);
});

it("labels a tab with an empty agent_id without collapsing the separator into the visible text", () => {
  render(
    <RunningLaneTabs
      lanes={[lane({ run_id: "r1", agent_id: "", skill_name: "", status: "running" })]}
      selectedRunId="r1"
      onSelect={jest.fn()}
    />,
  );

  expect(screen.getByRole("tab")).toHaveTextContent("running");
});

it("fires onSelect once per click, in click order, for rapid successive clicks on different tabs", () => {
  const onSelect = jest.fn();
  render(
    <RunningLaneTabs
      lanes={[lane({ run_id: "r1" }), lane({ run_id: "r2", agent_id: "gatekeeper" }), lane({ run_id: "r3", agent_id: "static_qa" })]}
      selectedRunId="r1"
      onSelect={onSelect}
    />,
  );

  fireEvent.click(screen.getByRole("tab", { name: /gatekeeper/ }));
  fireEvent.click(screen.getByRole("tab", { name: /static_qa/ }));
  fireEvent.click(screen.getByRole("tab", { name: /planner|backend_implementer/ }));

  expect(onSelect).toHaveBeenNthCalledWith(1, "r2");
  expect(onSelect).toHaveBeenNthCalledWith(2, "r3");
  expect(onSelect).toHaveBeenNthCalledWith(3, "r1");
  expect(onSelect).toHaveBeenCalledTimes(3);
});

it("updates a tab's label when its ledger status changes, without remounting it", () => {
  const { rerender } = render(
    <RunningLaneTabs lanes={[lane({ run_id: "r1", status: "running" })]} selectedRunId="r1" onSelect={jest.fn()} />,
  );
  expect(screen.getByRole("tab")).toHaveTextContent("running");

  rerender(
    <RunningLaneTabs
      lanes={[lane({ run_id: "r1", status: "awaiting_permission" })]}
      selectedRunId="r1"
      onSelect={jest.fn()}
    />,
  );

  expect(screen.getByRole("tab")).toHaveTextContent("awaiting_permission");
  expect(screen.getAllByRole("tab")).toHaveLength(1);
});
