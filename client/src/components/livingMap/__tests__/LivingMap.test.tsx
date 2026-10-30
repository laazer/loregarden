import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";

import type { LivingMap as LivingMapData, MapItem } from "../../../api/livingMapApi";
import { LivingMap } from "../LivingMap";

const mockGet = jest.fn();
jest.mock("../../../api/livingMapApi", () => ({
  livingMapApi: { get: (...args: unknown[]) => mockGet(...args) },
}));

function item(overrides: Partial<MapItem>): MapItem {
  return {
    id: "i1",
    seq: 1,
    kind: "question",
    state: "open",
    source: "record",
    title: "A question",
    detail: "",
    topic_id: null,
    happened_at: "2026-10-27T10:00:00Z",
    ...overrides,
  };
}

const ITEMS: MapItem[] = [
  item({ id: "t1", seq: 1, kind: "topic", title: "Release checklist" }),
  item({ id: "q1", seq: 2, kind: "question", state: "waiting", title: "Ship on Friday?", topic_id: "t1" }),
  item({ id: "k1", seq: 3, kind: "task", state: "settled", title: "Cut the branch", topic_id: "t1" }),
  item({ id: "r1", seq: 4, kind: "change", state: "failed", title: "backend_implementer ran implement" }),
];

function map(items: MapItem[] = ITEMS, sources: LivingMapData["sources"] = []): LivingMapData {
  return { scope: "chat", scope_id: "chat-1", title: "Plan the release", items, sources };
}

function renderMap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<LivingMap scope="chat" scopeRef="chat-1" />, { wrapper });
}

const rowTitles = () =>
  [...document.querySelectorAll(".living-map-title")].map((node) => node.textContent?.replace(/^.*?: /, ""));

beforeEach(() => {
  mockGet.mockReset();
});

describe("LivingMap", () => {
  it("draws every item on the vine in the order it happened, each named by kind and state", async () => {
    mockGet.mockResolvedValue(map());
    renderMap();

    const list = await screen.findByRole("list", { name: "4 of 4 items, in the order they happened" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(4);
    expect(rowTitles()).toEqual(["Release checklist", "Ship on Friday?", "Cut the branch", "backend_implementer ran implement"]);
    expect(screen.getByRole("button", { name: "Question, Waits on you: Ship on Friday?" })).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith("chat", "chat-1");
  });

  it("shows one lens at a time, with a count on each", async () => {
    mockGet.mockResolvedValue(map());
    renderMap();

    const waiting = await screen.findByRole("button", { name: /Waiting/ });
    expect(waiting).toHaveTextContent("1");
    fireEvent.click(waiting);

    expect(waiting).toHaveAttribute("aria-pressed", "true");
    expect(rowTitles()).toEqual(["Ship on Friday?"]);
    fireEvent.click(screen.getByRole("button", { name: /Changed/ }));
    expect(rowTitles()).toEqual(["backend_implementer ran implement"]);
  });

  it("focuses a topic: its tendril is drawn, the rest dims, and nothing moves", async () => {
    mockGet.mockResolvedValue(map());
    const { container } = renderMap();
    await screen.findByRole("list");
    const before = rowTitles();

    fireEvent.click(screen.getByRole("button", { name: /^Topic.*Release checklist/ }));

    expect(rowTitles()).toEqual(before);
    expect(container.querySelector(".living-map-tendril path")?.getAttribute("d")).toBeTruthy();
    const rows = [...container.querySelectorAll(".living-map-row")];
    expect(rows.map((row) => row.classList.contains("is-dim"))).toEqual([false, false, false, true]);
    expect(screen.getByRole("button", { name: /Release checklist/, expanded: true })).toBeInTheDocument();
  });

  it("shows a topic chip only where the heading above is a different topic", async () => {
    mockGet.mockResolvedValue(
      map([
        ...ITEMS.slice(0, 3),
        item({ id: "t2", seq: 4, kind: "topic", title: "Launch post" }),
        item({ id: "k2", seq: 5, kind: "task", title: "Tag the release", topic_id: "t1" }),
      ]),
    );
    const { container } = renderMap();
    await screen.findByRole("list");

    const chips = [...container.querySelectorAll(".living-map-row")].map(
      (row) => row.querySelector(".living-map-topic")?.textContent ?? null,
    );
    expect(chips).toEqual([null, null, null, null, "Release checklist"]);

    fireEvent.click(screen.getByRole("button", { name: "Release checklist", pressed: false }));
    expect(screen.getByRole("button", { name: "Release checklist", pressed: true })).toBeInTheDocument();
    expect(container.querySelector(".living-map-tendril path")?.getAttribute("d")).toBeTruthy();
  });

  it("folds back-to-back identical runs into one row that counts them", async () => {
    const run = (id: string, seq: number, state: MapItem["state"], at: string) =>
      item({ id, seq, kind: "change", state, title: "scheduler ran classify", happened_at: at });
    mockGet.mockResolvedValue(
      map([
        run("r1", 1, "failed", "2026-10-27T10:00:00Z"),
        run("r2", 2, "failed", "2026-10-27T10:05:00Z"),
        run("r3", 3, "failed", "2026-10-27T10:10:00Z"),
        run("r4", 4, "settled", "2026-10-27T10:15:00Z"),
      ]),
    );
    renderMap();

    const list = await screen.findByRole("list", { name: "4 of 4 items in 2 rows, in the order they happened" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    const folded = screen.getByRole("button", { name: "Run, Failed, 3 times: scheduler ran classify" });
    expect(list.querySelector(".living-map-times")).toHaveTextContent("×3");

    fireEvent.click(folded);
    expect(screen.getByRole("region", { name: "Details: scheduler ran classify" })).toHaveTextContent("3 times");
  });

  it("opens details just under the clicked row, and Escape hands focus back to it", async () => {
    mockGet.mockResolvedValue(map());
    renderMap();

    const row = await screen.findByRole("button", { name: /Cut the branch/ });
    fireEvent.click(row);
    const detail = screen.getByRole("region", { name: "Details: Cut the branch" });
    expect(detail.style.top).toBe("96px");
    expect(row).toHaveAttribute("aria-controls", detail.id);

    fireEvent.keyDown(detail, { key: "Escape" });
    expect(screen.queryByRole("region", { name: /Details/ })).not.toBeInTheDocument();
    expect(row).toHaveFocus();
    expect(row).toHaveAttribute("aria-expanded", "false");
  });

  it("grows at the tip: a new item leaves every row above it where it was", async () => {
    mockGet.mockResolvedValueOnce(map());
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { container } = render(<LivingMap scope="chat" scopeRef="chat-1" />, { wrapper });
    await screen.findByRole("list");
    const nodes = () =>
      [...container.querySelectorAll<HTMLElement>(".vine-node")].map((node) => [node.dataset.mark, node.className]);
    const before = nodes();

    mockGet.mockResolvedValueOnce(map([...ITEMS, item({ id: "q2", seq: 5, title: "Who announces it?" })]));
    await client.refetchQueries();
    await screen.findByText("Who announces it?");

    const after = nodes();
    expect(after).toHaveLength(5);
    expect(after.slice(0, 4)).toEqual(before);
  });

  it("opens an item's details, and names what a source could not read", async () => {
    mockGet.mockResolvedValue(
      map(
        [item({ id: "a1", kind: "assumption", state: "settled", title: "Assumed UTC", detail: "Assumed UTC everywhere, per the spec." })],
        [{ name: "checkpoints", count: 0, ok: false, error: "No Obsidian vault is configured" }],
      ),
    );
    renderMap();

    const row = await screen.findByRole("button", { name: /Assumed UTC/ });
    fireEvent.click(row);

    expect(row).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Assumed UTC everywhere, per the spec.")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("No Obsidian vault is configured");
  });

  it("says what would be here when it is empty, apart from when it failed", async () => {
    mockGet.mockResolvedValueOnce(map([]));
    const { unmount } = renderMap();
    expect(await screen.findByText(/Nothing on this map yet/)).toBeInTheDocument();
    unmount();

    mockGet.mockRejectedValueOnce(new Error("database is locked"));
    renderMap();
    expect(await screen.findByRole("alert")).toHaveTextContent("database is locked");
    expect(screen.queryByText(/Nothing on this map yet/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("adds a filter past fifty items", async () => {
    const many = Array.from({ length: 60 }, (_, index) =>
      item({ id: `q${index}`, seq: index + 1, title: index === 41 ? "Rotate the keys" : `Question ${index}` }),
    );
    mockGet.mockResolvedValue(map(many));
    renderMap();

    fireEvent.change(await screen.findByRole("textbox", { name: "Filter the map" }), { target: { value: "rotate" } });
    expect(rowTitles()).toEqual(["Rotate the keys"]);
  });
});
