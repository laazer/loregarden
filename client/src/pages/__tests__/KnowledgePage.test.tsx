import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { api } from "../../api/client";
import { ApiError } from "../../api/http";
import type { GraphNode, KnowledgeGraph, MemoryNodeDetail } from "../../api/memoryApi";
import { TopbarPageSlot, TopbarPageSlotProvider } from "../../components/TopbarPageSlot";
import { KnowledgePage } from "../KnowledgePage";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function node(id: string, overrides: Partial<GraphNode> = {}): GraphNode {
  return {
    id,
    title: `Record ${id}`,
    excerpt: `excerpt ${id}`,
    node_type: "learning",
    tags: [],
    ticket_id: "",
    discredited: false,
    created_at: "2026-09-01T00:00:00",
    updated_at: "2026-09-01T00:00:00",
    origin_kind: null,
    origin_ref: null,
    ...overrides,
  };
}

function graph(overrides: Partial<KnowledgeGraph> = {}): KnowledgeGraph {
  const nodes = overrides.nodes ?? [node("n1"), node("n2", { discredited: true })];
  return {
    workspace_slug: "lg",
    configured: true,
    source: "list",
    query: "",
    node_type: null,
    include_discredited: false,
    nodes,
    relations: [
      {
        id: "r1",
        source_id: "n1",
        target_id: "n2",
        relation_type: "supports",
        created_at: "2026-09-01T00:00:00",
      },
    ],
    counts: { entities: nodes.length, links: 1 },
    type_counts: { learning: nodes.length },
    truncated: false,
    checked_at: "2026-09-27T10:00:00Z",
    ...overrides,
  };
}

function record(id: string, overrides: Partial<MemoryNodeDetail> = {}): MemoryNodeDetail {
  return {
    ...node(id),
    body: `Body of **${id}**`,
    aliases: [],
    workspace_slug: "lg",
    confidence: { mean: 0.5, lower_bound: 0, observations: 0, trusted: false },
    versions: [],
    ladder: { clean_pass: 0, passed_after_autofix: 0, rerouted: 0, blocked: 0 },
    relations: [
      {
        id: "r1",
        relation_type: "supports",
        direction: "out",
        node_id: "n2",
        title: "Record n2",
        discredited: true,
      },
    ],
    superseded_by: [],
    ...overrides,
  };
}

function Where() {
  return <span data-testid="where">{useLocation().pathname}</span>;
}

function renderAt(path = "/knowledge") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <TopbarPageSlotProvider>
          <TopbarPageSlot />
          <Routes>
            <Route path="/knowledge" element={<KnowledgePage />} />
            <Route path="/knowledge/:nodeId" element={<KnowledgePage />} />
          </Routes>
          <Where />
        </TopbarPageSlotProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.workspaces.mockResolvedValue([
    { id: "w1", slug: "lg", name: "loregarden" } as Awaited<
      ReturnType<typeof api.workspaces>
    >[number],
  ]);
  mockApi.memoryGraph.mockResolvedValue(graph());
  mockApi.memoryNode.mockImplementation(async (id) => record(id, { discredited: id === "n2" }));
});

const toList = () => fireEvent.click(screen.getByRole("radio", { name: "List" }));

it("frames the canvas and skeletons the counts while loading", async () => {
  mockApi.memoryGraph.mockReturnValue(new Promise(() => {}));
  renderAt();
  const skeleton = await screen.findByText("Loading the graph…");
  expect(skeleton.closest(".kb-canvas")).not.toBeNull();
  expect(screen.getByLabelText("Loading counts")).toBeInTheDocument();
});

it("draws the records, marks the discredited one, and states the legend", async () => {
  renderAt();
  const canvas = await screen.findByTestId("knowledge-canvas");
  expect(within(canvas).getAllByTestId("react-flow-node")).toHaveLength(2);
  expect(screen.getByText(/2 records · 1 links/)).toBeInTheDocument();
  // The acceptance criterion: position carries no meaning, said in words.
  expect(screen.getByLabelText("Legend")).toHaveTextContent(/visual grouping only/);
});

it("says no memories exist, distinctly from not configured and from an error", async () => {
  mockApi.memoryGraph.mockResolvedValue(graph({ nodes: [], relations: [] }));
  renderAt();
  const state = await screen.findByRole("heading", { name: /no memories recorded yet/i });
  expect(state).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("renders a missing graph as a setup state", async () => {
  mockApi.memoryGraph.mockResolvedValue(
    graph({ configured: false, nodes: [], relations: [], type_counts: {} }),
  );
  renderAt();
  expect(
    await screen.findByRole("heading", { name: /no memory graph is configured/i }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: /no memories recorded yet/i })).toBeNull();
});

it("names a filter that matched nothing and clears it", async () => {
  renderAt();
  await screen.findByTestId("knowledge-canvas");
  mockApi.memoryGraph.mockResolvedValue(
    graph({ nodes: [], relations: [], query: "zebra", source: "search" }),
  );
  fireEvent.change(screen.getByRole("searchbox", { name: /find in records/i }), {
    target: { value: "zebra" },
  });
  await waitFor(() =>
    expect(mockApi.memoryGraph).toHaveBeenLastCalledWith("lg", {
      query: "zebra",
      nodeType: null,
      includeDiscredited: false,
    }),
  );
  const heading = await screen.findByRole("heading", { name: /zebra/ });
  expect(heading).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /clear filter/i }));
  expect(screen.getByRole("searchbox", { name: /find in records/i })).toHaveValue("");
});

it("shows a failed load as an error with a retry, not as empty", async () => {
  mockApi.memoryGraph.mockRejectedValue(new Error("server down"));
  renderAt();
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("server down");
  mockApi.memoryGraph.mockResolvedValue(graph());
  fireEvent.click(within(alert).getByRole("button", { name: /try again/i }));
  expect(await screen.findByTestId("knowledge-canvas")).toBeInTheDocument();
});

it("disables Refresh and marks it busy while a refresh is in flight", async () => {
  renderAt();
  await screen.findByTestId("knowledge-canvas");
  mockApi.memoryGraph.mockReturnValue(new Promise(() => {}));
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  const busy = await screen.findByRole("button", { name: /refreshing/i });
  expect(busy).toBeDisabled();
  expect(busy).toHaveAttribute("aria-busy", "true");
});

it("selecting a record does not refetch the graph", async () => {
  renderAt();
  await screen.findByTestId("knowledge-canvas");
  toList();
  const calls = mockApi.memoryGraph.mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: /record n1/i }));
  expect(await screen.findByRole("heading", { name: "Record n1" })).toBeInTheDocument();
  expect(mockApi.memoryGraph.mock.calls.length).toBe(calls);
  expect(screen.getByTestId("where")).toHaveTextContent("/knowledge/n1");
});

it("is operable from the list by keyboard, and Escape closes the panel", async () => {
  renderAt();
  await screen.findByTestId("knowledge-canvas");
  toList();
  const rows = within(screen.getByRole("list", { name: "Records" })).getAllByRole("button");
  expect(rows).toHaveLength(2);
  fireEvent.click(rows[1]);
  const panel = await screen.findByRole("dialog");
  const title = await within(panel).findByRole("heading", { name: "Record n2" });
  // A labelled pill beside the title, not colour alone.
  expect(title.nextElementSibling).toHaveTextContent("Discredited");
  fireEvent.keyDown(document, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.getByTestId("where")).toHaveTextContent(/^\/knowledge$/);
});

it("selects the record a deep link names", async () => {
  renderAt("/knowledge/n1");
  const panel = await screen.findByRole("dialog");
  expect(await within(panel).findByRole("heading", { name: "Record n1" })).toBeInTheDocument();
  expect(mockApi.memoryNode).toHaveBeenCalledWith("n1", "lg");
});

it("names an unknown deep-link id and offers a way back", async () => {
  mockApi.memoryNode.mockRejectedValue(new ApiError(404, "No memory node"));
  renderAt("/knowledge/missing%2Fid");
  const panel = await screen.findByRole("dialog");
  expect(
    await within(panel).findByRole("heading", { name: /record not found/i }),
  ).toBeInTheDocument();
  expect(within(panel).getByRole("link", { name: /back to all records/i })).toHaveAttribute(
    "href",
    "/knowledge",
  );
  expect(mockApi.memoryNode).toHaveBeenCalledWith("missing/id", "lg");
});

it("a neighbour click moves the selection", async () => {
  renderAt("/knowledge/n1");
  const panel = await screen.findByRole("dialog");
  fireEvent.click(await within(panel).findByRole("button", { name: "Record n2" }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/knowledge/n2"));
  expect(mockApi.memoryNode).toHaveBeenCalledWith("n2", "lg");
});

it("says the origin is unknown when provenance was never recorded", async () => {
  renderAt("/knowledge/n1");
  const origin = await screen.findByTestId("kb-origin");
  expect(origin).toHaveTextContent(/^Origin unknown · recorded /);
});

it("shows a recorded agent origin, and that its reference is missing", async () => {
  mockApi.memoryNode.mockResolvedValue(record("n1", { origin_kind: "agent", origin_ref: null }));
  renderAt("/knowledge/n1");
  const origin = await screen.findByTestId("kb-origin");
  expect(origin).toHaveTextContent(/^Agent · reference not recorded · /);
});

it("a graph node click selects that record", async () => {
  renderAt();
  const canvas = await screen.findByTestId("knowledge-canvas");
  fireEvent.click(within(canvas).getAllByTestId("react-flow-node")[0]);
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/knowledge/n1"));
});

it("asks for discredited records only when told to", async () => {
  renderAt();
  await screen.findByTestId("knowledge-canvas");
  fireEvent.click(screen.getByRole("checkbox", { name: /show discredited/i }));
  await waitFor(() =>
    expect(mockApi.memoryGraph).toHaveBeenLastCalledWith("lg", {
      query: "",
      nodeType: null,
      includeDiscredited: true,
    }),
  );
});
