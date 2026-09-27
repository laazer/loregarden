import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { api } from "../../api/client";
import { ApiError } from "../../api/http";
import type { GraphNode, KnowledgeGraph, MemoryNodeDetail } from "../../api/memoryApi";
import { TopbarPageSlot, TopbarPageSlotProvider } from "../../components/TopbarPageSlot";
import { LegacyKnowledgeRedirect, MemoryPage } from "../MemoryPage";

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

function renderAt(path = "/memory") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <TopbarPageSlotProvider>
          <TopbarPageSlot />
          <Routes>
            <Route path="/memory/*" element={<MemoryPage />} />
            <Route path="/knowledge/*" element={<LegacyKnowledgeRedirect />} />
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
const mapNodes = () => within(screen.getByTestId("knowledge-canvas")).getAllByTestId("mm-node");

it("frames the canvas and skeletons the counts while loading", async () => {
  mockApi.memoryGraph.mockReturnValue(new Promise(() => {}));
  renderAt();
  const skeleton = await screen.findByText("Loading the map…");
  expect(skeleton.closest(".mm-canvas")).not.toBeNull();
  expect(screen.getByLabelText("Loading counts")).toBeInTheDocument();
});

it("draws the records, marks the discredited one, and states the legend", async () => {
  renderAt();
  await screen.findByTestId("knowledge-canvas");
  expect(mapNodes()).toHaveLength(2);
  expect(screen.getByRole("button", { name: /Record n2 .*discredited/ })).toHaveClass(
    "mm-node--discredited",
  );
  expect(screen.getByText(/2 records · 1 links/)).toBeInTheDocument();
  // Position is said in words to mean only "linked", never "similar".
  expect(screen.getByLabelText("Legend")).toHaveTextContent(/distance is not similarity/);
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
  const list = screen.getByRole("list", { name: "Records" });
  fireEvent.click(within(list).getByRole("button", { name: /record n1/i }));
  expect(await screen.findByRole("heading", { name: "Record n1" })).toBeInTheDocument();
  expect(mockApi.memoryGraph.mock.calls.length).toBe(calls);
  expect(screen.getByTestId("where")).toHaveTextContent("/memory/map/n1");
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
  expect(screen.getByTestId("where")).toHaveTextContent(/^\/memory$/);
});

it("selects the record a deep link names", async () => {
  renderAt("/memory/map/n1");
  const panel = await screen.findByRole("dialog");
  expect(await within(panel).findByRole("heading", { name: "Record n1" })).toBeInTheDocument();
  expect(mockApi.memoryNode).toHaveBeenCalledWith("n1", "lg");
});

it("names an unknown deep-link id and offers a way back", async () => {
  mockApi.memoryNode.mockRejectedValue(new ApiError(404, "No memory node"));
  renderAt("/memory/map/missing%2Fid");
  const panel = await screen.findByRole("dialog");
  expect(
    await within(panel).findByRole("heading", { name: /record not found/i }),
  ).toBeInTheDocument();
  expect(within(panel).getByRole("link", { name: /back to all records/i })).toHaveAttribute(
    "href",
    "/memory",
  );
  expect(mockApi.memoryNode).toHaveBeenCalledWith("missing/id", "lg");
});

it("a neighbour click moves the selection", async () => {
  renderAt("/memory/map/n1");
  const panel = await screen.findByRole("dialog");
  fireEvent.click(await within(panel).findByRole("button", { name: "Record n2" }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/memory/map/n2"));
  expect(mockApi.memoryNode).toHaveBeenCalledWith("n2", "lg");
});

it("says the origin is unknown when provenance was never recorded", async () => {
  renderAt("/memory/map/n1");
  const origin = await screen.findByTestId("kb-origin");
  expect(origin).toHaveTextContent(/^Origin unknown · recorded /);
});

it("shows a recorded agent origin, and that its reference is missing", async () => {
  mockApi.memoryNode.mockResolvedValue(record("n1", { origin_kind: "agent", origin_ref: null }));
  renderAt("/memory/map/n1");
  const origin = await screen.findByTestId("kb-origin");
  expect(origin).toHaveTextContent(/^Agent · reference not recorded · /);
});

it("a map node click selects that record", async () => {
  renderAt();
  await screen.findByTestId("knowledge-canvas");
  fireEvent.click(mapNodes()[0]);
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/memory/map/n1"));
});

it("selects a map node from the keyboard", async () => {
  renderAt();
  await screen.findByTestId("knowledge-canvas");
  const target = screen.getByRole("button", { name: /^Record n1 —/ });
  target.focus();
  fireEvent.keyDown(target, { key: "Enter" });
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/memory/map/n1"));
});

it("lights a record's neighbourhood on hover and dims the rest", async () => {
  mockApi.memoryGraph.mockResolvedValue(graph({ nodes: [node("n1"), node("n2"), node("n3")] }));
  renderAt();
  await screen.findByTestId("knowledge-canvas");
  fireEvent.mouseEnter(screen.getByRole("button", { name: /^Record n1 —/ }));
  const dimmed = mapNodes().filter((el) => el.classList.contains("mm-dim"));
  expect(dimmed.map((el) => el.getAttribute("data-node-id"))).toEqual(["n3"]);
});

it("hides a link type from the legend and says so", async () => {
  renderAt();
  await screen.findByTestId("knowledge-canvas");
  expect(screen.getAllByTestId("mm-edge")).toHaveLength(1);
  const toggle = screen.getByRole("button", { name: "Supports" });
  expect(toggle).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-pressed", "false");
  expect(screen.queryAllByTestId("mm-edge")).toHaveLength(0);
});

it("gives an overview beside the map: contradictions, busiest and unlinked", async () => {
  mockApi.memoryGraph.mockResolvedValue(
    graph({
      nodes: [node("n1"), node("n2"), node("n3")],
      relations: [
        {
          id: "r1",
          source_id: "n1",
          target_id: "n2",
          relation_type: "contradicts",
          created_at: "2026-09-01T00:00:00",
        },
      ],
    }),
  );
  renderAt();
  const overview = await screen.findByRole("complementary", { name: "Map overview" });
  const contradictions = within(overview).getByRole("heading", { name: "Contradictions" })
    .parentElement!;
  expect(within(contradictions).getByRole("button", { name: "Record n1" })).toBeInTheDocument();
  expect(within(contradictions).getByRole("button", { name: "Record n2" })).toBeInTheDocument();
  const unlinked = within(overview).getByRole("heading", { name: "Unlinked" }).parentElement!;
  fireEvent.click(within(unlinked).getByRole("button", { name: "Record n3" }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/memory/map/n3"));
  // The record's panel takes the overview's place.
  expect(screen.queryByRole("complementary", { name: "Map overview" })).toBeNull();
});

it("says there are no contradictions rather than showing an empty list", async () => {
  renderAt();
  const overview = await screen.findByRole("complementary", { name: "Map overview" });
  expect(overview).toHaveTextContent("No recorded contradictions among these records.");
});

it("sends an old /knowledge link to the same record on the map", async () => {
  renderAt("/knowledge/n1");
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/memory/map/n1"));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
});

it("switches tabs by link, with the current one marked", async () => {
  renderAt();
  const tabs = await screen.findByRole("navigation", { name: "Memory views" });
  expect(within(tabs).getByRole("link", { name: "Map" })).toHaveAttribute("aria-current", "page");
  expect(within(tabs).getByRole("link", { name: "Health" })).toHaveAttribute(
    "href",
    "/memory/health",
  );
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
