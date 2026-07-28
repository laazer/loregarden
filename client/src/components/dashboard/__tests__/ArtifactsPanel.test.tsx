import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api } from "../../../api/client";
import { ArtifactsPanel } from "../ArtifactsPanel";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function renderPanel(isActive = false) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <ArtifactsPanel ticketId="t1" isActive={isActive} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
});

it("lists artifacts newest-first with kind filter", async () => {
  mockApi.ticketArtifacts.mockResolvedValue({
    total: 2,
    items: [
      {
        id: "a2",
        kind: "test_spec",
        title: "API gates_enabled Tests",
        run_id: null,
        evidence_kind: "",
        commit_sha: "",
        created_at: "2026-07-28T12:00:41Z",
        content_bytes: 120,
        content: { cases: ["enabled"] },
      },
      {
        id: "a1",
        kind: "source_analysis",
        title: "gate_runner.py",
        run_id: "r1",
        evidence_kind: "",
        commit_sha: "",
        created_at: "2026-07-28T11:56:53Z",
        content_bytes: 80,
        content: { summary: "ok" },
      },
    ],
  });

  renderPanel();

  expect(await screen.findByText("2 artifacts")).toBeInTheDocument();
  expect(screen.getByText("API gates_enabled Tests")).toBeInTheDocument();
  expect(screen.getByText("gate_runner.py")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /test_spec · 1/ }));
  expect(screen.getByText("API gates_enabled Tests")).toBeInTheDocument();
  expect(screen.queryByText("gate_runner.py")).not.toBeInTheDocument();
});

it("expands content JSON on click", async () => {
  mockApi.ticketArtifacts.mockResolvedValue({
    total: 1,
    items: [
      {
        id: "a1",
        kind: "analysis",
        title: "Plan",
        run_id: null,
        evidence_kind: "",
        commit_sha: "",
        created_at: "2026-07-28T11:55:58Z",
        content_bytes: 40,
        content: { note: "local model dump" },
      },
    ],
  });

  renderPanel(true);
  expect(await screen.findByText(/1 artifact · live/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /Plan/ }));
  await waitFor(() => {
    expect(screen.getByText(/"note": "local model dump"/)).toBeInTheDocument();
  });
});

it("shows empty state when feed is empty", async () => {
  mockApi.ticketArtifacts.mockResolvedValue({ total: 0, items: [] });
  renderPanel();
  expect(await screen.findByText("No artifacts yet")).toBeInTheDocument();
});

it("filters by search across title and content", async () => {
  mockApi.ticketArtifacts.mockResolvedValue({
    total: 2,
    items: [
      {
        id: "a2",
        kind: "test_spec",
        title: "API gates_enabled Tests",
        run_id: null,
        evidence_kind: "",
        commit_sha: "",
        created_at: "2026-07-28T12:00:41Z",
        content_bytes: 120,
        content: { cases: ["enabled"] },
      },
      {
        id: "a1",
        kind: "source_analysis",
        title: "gate_runner.py",
        run_id: "r1",
        evidence_kind: "",
        commit_sha: "",
        created_at: "2026-07-28T11:56:53Z",
        content_bytes: 80,
        content: { summary: "fail-open path" },
      },
    ],
  });

  renderPanel();
  expect(await screen.findByText("2 artifacts")).toBeInTheDocument();

  fireEvent.change(screen.getByRole("searchbox", { name: "Search artifacts" }), {
    target: { value: "fail-open" },
  });

  expect(screen.getByText("1 of 2")).toBeInTheDocument();
  expect(screen.getByText("gate_runner.py")).toBeInTheDocument();
  expect(screen.queryByText("API gates_enabled Tests")).not.toBeInTheDocument();
});
