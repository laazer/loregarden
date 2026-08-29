/**
 * 557 — the chat primitives as view containers.
 *
 * Covers the ticket's AC1 (every one of the 23 has a recorded verdict), AC2
 * (each adaptable one is registered, renders from settings and fetches its own
 * data), AC3 (the rest are recorded and absent), AC7 (two instances coexist)
 * and AC8 (no page-level state).
 *
 * Everything here goes through `ContainerPrimitiveHost`, never through a chat
 * component imported directly: the host is the only path a view has, and it is
 * where `parseSettings` runs. A test that mounted `TicketPrimitive` with a
 * hand-built part would pass without any of this ticket's code existing.
 */

import fs from "fs";
import path from "path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api } from "../../../api/client";
import { fetchBranchActivity } from "../../../lib/branchTriageApi";
import type { TicketDetail } from "../../../api/types";
import { CHAT_PANE_PRIMITIVES } from "../primitives/chatPanePrimitives";
import { chatPaneId } from "../primitives/chatPanePrimitive";
import {
  CHAT_PRIMITIVE_VERDICTS,
  adaptablePrimitiveKinds,
  chatBoundPrimitiveKinds,
} from "../primitives/chatPrimitiveVerdicts";
import { PRIMITIVE_RENDERERS } from "../../chat/primitives/registry";
import { CONTAINER_PRIMITIVES, ContainerPrimitiveHost } from "../primitives/registry";

jest.mock("../../../api/client");
jest.mock("../../../lib/branchTriageApi");

const mockApi = api as jest.Mocked<typeof api>;
const mockBranchActivity = fetchBranchActivity as jest.MockedFunction<typeof fetchBranchActivity>;

beforeEach(() => {
  jest.clearAllMocks();
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe(): void {}
    disconnect(): void {}
    unobserve(): void {}
  };
});

afterEach(() => jest.restoreAllMocks());

function ticket(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    id: "t-1",
    external_id: "lg-1",
    title: "A ticket",
    state: "in_progress",
    priority: 2,
    workspace_slug: "loregarden",
    workflow_stage_key: "implement",
    workflow_stage_name: "Implement",
    workflow_stage_status: "pending",
    stages: [],
    acceptance_criteria: [],
    ...overrides,
  } as TicketDetail;
}

function renderHost(containerId: string, settings: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContainerPrimitiveHost containerId={containerId} settings={settings} />
    </QueryClientProvider>,
  );
}

describe("AC1 — every one of the 23 chat primitives has a recorded verdict", () => {
  it("covers the chat registry exactly, with no primitive left undecided", () => {
    // `CHAT_PRIMITIVE_VERDICTS` is a `Record<PrimitiveKind, …>`, so a 24th
    // chat primitive fails the *build*. That is the real guard; this asserts
    // the other direction — that `PrimitiveKind` and the chat registry the
    // agent actually renders through have not drifted apart, which the type
    // cannot see.
    expect(Object.keys(CHAT_PRIMITIVE_VERDICTS).sort()).toEqual(
      Object.keys(PRIMITIVE_RENDERERS).sort(),
    );
    expect(Object.keys(CHAT_PRIMITIVE_VERDICTS)).toHaveLength(23);
  });

  it("records evidence and a reason for each, not a bare verdict", () => {
    // AC1 asks for "the reason and the evidence for it". A verdict with an
    // empty reason is the shape this ticket was told not to ship.
    for (const [kind, audit] of Object.entries(CHAT_PRIMITIVE_VERDICTS)) {
      expect({ kind, verdict: audit.verdict }).toEqual({
        kind,
        verdict: expect.stringMatching(/^(adaptable|adaptable-with-wrapper|chat-bound)$/),
      });
      expect({ kind, needs: audit.needs.length > 20 }).toEqual({ kind, needs: true });
      expect({ kind, reason: audit.reason.length > 20 }).toEqual({ kind, reason: true });
    }
  });

  it("splits the 23 into the thirteen it registers and the ten it does not", () => {
    expect(adaptablePrimitiveKinds()).toHaveLength(13);
    expect(chatBoundPrimitiveKinds()).toHaveLength(10);
  });
});

describe("AC2 / AC3 — the verdict and the registry are the same decision", () => {
  it("registers every primitive the audit calls adaptable", () => {
    const registered = new Set(CONTAINER_PRIMITIVES.map((entry) => entry.id));
    for (const kind of adaptablePrimitiveKinds()) {
      expect({ kind, registered: registered.has(chatPaneId(kind)) }).toEqual({
        kind,
        registered: true,
      });
    }
    expect(CHAT_PANE_PRIMITIVES).toHaveLength(adaptablePrimitiveKinds().length);
  });

  it("leaves out every primitive the audit calls chat-bound", () => {
    // AC3's clause, and the `EXCLUDED_PANELS` precedent: absent, not
    // half-wired. Checked against the registry rather than against the module,
    // because a chat-bound primitive smuggled in under a different id would
    // still have to appear there to be mountable.
    const registered = new Set(CONTAINER_PRIMITIVES.map((entry) => entry.id));
    for (const kind of chatBoundPrimitiveKinds()) {
      expect({ kind, registered: registered.has(chatPaneId(kind)) }).toEqual({
        kind,
        registered: false,
      });
    }
  });

  it("gives every registered chat pane the settings fields 554's editor needs", () => {
    // "A primitive with no way to be given its identifier is not adaptable."
    // A registration whose schema is empty renders a pane nobody can point at
    // a ticket, and 554's editor generates its inputs from exactly this list.
    for (const entry of CHAT_PANE_PRIMITIVES) {
      expect({ id: entry.id, fields: entry.settingsFields.length }).toEqual({
        id: entry.id,
        fields: expect.any(Number),
      });
      expect(entry.settingsFields.length).toBeGreaterThan(0);
      for (const field of entry.settingsFields) {
        expect(field.help ?? "").not.toBe("");
      }
    }
  });
});

describe("AC2 — a pane renders from its settings and fetches its own data", () => {
  it("fetches the ticket its settings name, and shows what came back", async () => {
    mockApi.ticket.mockResolvedValue(ticket({ title: "Adapted in a pane" }));

    renderHost("c1", { primitive_id: "chat_ticket", ticket_id: "t-42" });

    expect(await screen.findByText("Adapted in a pane")).toBeInTheDocument();
    expect(mockApi.ticket).toHaveBeenCalledWith("t-42");
  });

  it("fetches a branch's history from the workspace, branch and limit in settings", async () => {
    // Not a ticket primitive: this one reaches a different module entirely, so
    // it proves the adapter is not a ticket-shaped special case. It is also the
    // only registered pane with a *number* setting, and the call is where that
    // number is observable — a `limit` dropped on the floor by `parseSettings`
    // still renders a perfectly convincing card.
    mockBranchActivity.mockResolvedValue({
      branch: "main",
      upstream: "origin/main",
      commits: [],
    } as unknown as Awaited<ReturnType<typeof fetchBranchActivity>>);

    renderHost("c1", {
      primitive_id: "chat_branch_history",
      workspace_slug: "loregarden",
      branch: "main",
      limit: 3,
    });

    await waitFor(() =>
      expect(mockBranchActivity).toHaveBeenCalledWith("loregarden", "main", 3),
    );
  });

  it("falls back to the declared limit when the stored one is unusable", async () => {
    // 444's shape for a number field: a non-finite number is stored as `null`.
    // A `limit` of 0 or null asks for a page of nothing; the schema default is
    // what must reach the wire instead.
    mockBranchActivity.mockResolvedValue({
      branch: "main",
      upstream: null,
      commits: [],
    } as unknown as Awaited<ReturnType<typeof fetchBranchActivity>>);

    // `null` alone is too weak to be the whole test: a `typeof value !==
    // "number"` guard handles it and still lets `0` and `-3` through to the
    // wire. Each unusable value is asserted, and 12.7 pins the flooring.
    for (const [index, limit] of [null, 0, -3, Number.NaN, "8"].entries()) {
      mockBranchActivity.mockClear();
      renderHost(`c${index}`, {
        primitive_id: "chat_branch_history",
        workspace_slug: "loregarden",
        branch: "main",
        limit,
      });
      await waitFor(() =>
        expect(mockBranchActivity).toHaveBeenCalledWith("loregarden", "main", 8),
      );
    }

    mockBranchActivity.mockClear();
    renderHost("cf", {
      primitive_id: "chat_branch_history",
      workspace_slug: "loregarden",
      branch: "main",
      limit: 12.7,
    });
    await waitFor(() =>
      expect(mockBranchActivity).toHaveBeenCalledWith("loregarden", "main", 12),
    );
  });

  it("waits for its identifier instead of fetching an empty one", () => {
    // The `missing` sentinel, and the reason it exists: `api.ticket("")` is a
    // request for a ticket that cannot exist, and the "not found" it answers
    // with reads as a bug rather than an empty field.
    const { container } = renderHost("c1", { primitive_id: "chat_ticket" });
    expect(mockApi.ticket).not.toHaveBeenCalled();
    const host = container.querySelector("[data-container-id='c1']");
    expect(host).toHaveAttribute("data-primitive-id", "chat_ticket");
    expect(host).toHaveTextContent(/has no ticket yet/);
  });

  it("waits when only one of two required identifiers is filled in", () => {
    // A branch names no repository without a workspace. An `||` written as an
    // `&&` passes the test above and fetches on half a key.
    const { container } = renderHost("c1", {
      primitive_id: "chat_branch_history",
      branch: "main",
    });
    expect(container.querySelector("[data-container-id='c1']")).toHaveTextContent(
      /has no workspace and branch yet/,
    );
  });

  it("splits a comma-separated list setting into the list the part wants", async () => {
    // The wrapper the audit calls for, at the only place it is observable: two
    // ids in one field become two fetches, not one fetch for "a,b".
    mockApi.ticket.mockImplementation(async (id: string) => ticket({ id, title: `T ${id}` }));

    renderHost("c1", {
      primitive_id: "chat_kanban",
      ticket_ids: "t-a, t-b",
      statuses: "in_progress",
    });

    await waitFor(() => expect(mockApi.ticket).toHaveBeenCalledWith("t-a"));
    expect(mockApi.ticket).toHaveBeenCalledWith("t-b");
    // And the empty entry in "a,,b" never reaches the wire.
    expect(mockApi.ticket).not.toHaveBeenCalledWith("");
  });
});

describe("AC8 — a pane offers no control that would navigate the app away", () => {
  it("draws no Open control inside a pane, though the same card has one in chat", async () => {
    // The defect: `Open ticket` inside a pane pushes a route, which tears down
    // every other pane in the composed view to show one ticket. Suppressed
    // through `ResourceNavigationContext`, set once by the adapter.
    //
    // Both halves are asserted. Without the second, deleting the button
    // outright would pass — and would take the control away from the thread,
    // where it is the whole point of the card.
    mockApi.ticket.mockResolvedValue(ticket({ title: "Adapted in a pane" }));

    const { container } = renderHost("c1", {
      primitive_id: "chat_ticket",
      ticket_id: "t-42",
    });
    await screen.findByText("Adapted in a pane");
    const host = container.querySelector("[data-container-id='c1']") as HTMLElement;
    expect(within(host).queryByRole("button", { name: /^Open/ })).toBeNull();

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const TicketCard = PRIMITIVE_RENDERERS.ticket as React.ComponentType<{
      part: { primitive: "ticket"; ticket_id: string };
    }>;
    const inChat = render(
      <QueryClientProvider client={client}>
        <TicketCard part={{ primitive: "ticket", ticket_id: "t-42" }} />
      </QueryClientProvider>,
    );
    await waitFor(() =>
      expect(
        within(inChat.container).queryByRole("button", { name: /^Open ticket/ }),
      ).not.toBeNull(),
    );
  });
});

describe("AC7 — two panes of the same primitive coexist", () => {
  it("each shows its own ticket, and no DOM id is shared", async () => {
    mockApi.ticket.mockImplementation(async (id: string) =>
      ticket({ id, title: id === "t-left" ? "Left ticket" : "Right ticket" }),
    );

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ContainerPrimitiveHost
          containerId="left"
          settings={{ primitive_id: "chat_ticket", ticket_id: "t-left" }}
        />
        <ContainerPrimitiveHost
          containerId="right"
          settings={{ primitive_id: "chat_ticket", ticket_id: "t-right" }}
        />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Left ticket")).toBeInTheDocument();
    expect(await screen.findByText("Right ticket")).toBeInTheDocument();

    const ids = Array.from(document.querySelectorAll("[id]")).map((el) => el.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("lets two boards filter independently", async () => {
    // The one registered chat pane with interactive state of its own: the
    // filter toggles are seeded from the part. Held in a module-level variable
    // rather than `useState`, pressing one board's toggle would move the
    // other's — and every assertion above would still pass.
    mockApi.tickets.mockResolvedValue([]);
    mockApi.ticket.mockResolvedValue(ticket());

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(
      <QueryClientProvider client={client}>
        <ContainerPrimitiveHost
          containerId="left"
          settings={{
            primitive_id: "chat_filterable_kanban",
            statuses: "backlog,done",
            filters: "backlog,done",
          }}
        />
        <ContainerPrimitiveHost
          containerId="right"
          settings={{
            primitive_id: "chat_filterable_kanban",
            statuses: "backlog,done",
            filters: "backlog,done",
          }}
        />
      </QueryClientProvider>,
    );

    const boards = ["left", "right"].map(
      (id) => container.querySelector(`[data-container-id='${id}']`) as HTMLElement,
    );
    // The toggles live in the card body, which is hidden while the bucket
    // query is in flight — so this waits for the board, not for a timer.
    const leftToggle = await within(boards[0]).findByRole("button", { name: "Backlog" });
    const rightToggle = await within(boards[1]).findByRole("button", { name: "Backlog" });
    expect(leftToggle).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(leftToggle);

    await waitFor(() => expect(leftToggle).toHaveAttribute("aria-pressed", "false"));
    expect(rightToggle).toHaveAttribute("aria-pressed", "true");
  });
});

describe("AC8 — no registered pane reaches page-level state, transitively", () => {
  /**
   * The existing scan in `containerPrimitives.render.test` reads the *direct*
   * imports of the modules under `views/primitives/`. That was the whole graph
   * when every primitive was written there. It is not any more: a chat pane
   * mounts a component two directories away, and that component's own imports
   * are where a `uiStore` read would now hide. A direct-import scan cannot see
   * it, and would report clean over a `calendar` pane wired straight in.
   *
   * So this follows relative imports from each registered chat pane's module
   * and checks the closure. Node modules are not followed — the rule is about
   * this app's page-level state.
   */
  const FORBIDDEN = [
    "state/uiStore",
    "state/QueueStatusContext",
    "state/composerQueueStore",
    "state/notificationStore",
    "state/SidebarWorkspaceContext",
    "react-router",
    "useAppNavigation",
  ];

  /**
   * The one module in the closure that imports a forbidden one, and why it is
   * allowed to.
   *
   * `ResourceActionButton` is every `Open …` control in the chat vocabulary. It
   * is reached from nine of the thirteen panes, and it is *disarmed* rather
   * than avoided: `ResourceNavigationContext` is false inside a pane, so the
   * component returns null before it renders anything, and the test above
   * asserts that no `Open` control appears in a pane while one still does in a
   * thread. Its own `useUiStore` selectors resolve to store *actions*, which
   * hold no value two panes could disagree about.
   *
   * Recorded here, as one named exception with a reason, rather than left for
   * the scan not to notice. Anything else that appears in this closure is a
   * defect and fails.
   */
  const ALLOWED = ["chat/primitives/ResourceActionButton"];

  function resolveImport(fromFile: string, specifier: string): string | null {
    const base = path.resolve(path.dirname(fromFile), specifier);
    for (const candidate of [
      `${base}.tsx`,
      `${base}.ts`,
      path.join(base, "index.tsx"),
      path.join(base, "index.ts"),
    ]) {
      if (fs.existsSync(candidate)) return candidate;
    }
    return null;
  }

  it("imports no page-level state anywhere in a registered pane's module graph", () => {
    const roots = [
      path.resolve(__dirname, "../primitives/chatPanePrimitives.tsx"),
      path.resolve(__dirname, "../primitives/chatPanePrimitive.tsx"),
    ];
    const seen = new Set<string>();
    const queue = [...roots];
    const offenders: { file: string; moduleName: string }[] = [];

    while (queue.length > 0) {
      const file = queue.pop() as string;
      if (seen.has(file)) continue;
      seen.add(file);
      const source = fs.readFileSync(file, "utf8");
      const relative = file.replace(/\\/g, "/");

      // An allowed module is a *boundary*, not merely an unchecked node: the
      // graph is cut there because that is where the controls are disarmed, so
      // its own imports of the router and the store are the allowance itself
      // rather than three further offences.
      if (ALLOWED.some((allowed) => relative.includes(allowed))) continue;

      for (const moduleName of FORBIDDEN) {
        if (new RegExp(`from\\s*["'][^"']*${moduleName}`).test(source)) {
          offenders.push({ file: relative, moduleName });
        }
      }

      for (const [, specifier] of source.matchAll(/from\s*["'](\.[^"']+)["']/g)) {
        const resolved = resolveImport(file, specifier);
        if (resolved !== null) queue.push(resolved);
      }
    }

    // The closure has to be real, or this passes by walking nothing.
    expect(seen.size).toBeGreaterThan(20);
    expect(offenders).toEqual([]);
  });

  it("still sees the one module the allowance covers, so the allowance is not dead", () => {
    // An allowlist entry for a module no longer in the graph is a rule nobody
    // is following any more, and it would quietly excuse a future import.
    const button = path.resolve(
      __dirname,
      "../../chat/primitives/ResourceActionButton.tsx",
    );
    const source = fs.readFileSync(button, "utf8");
    expect(FORBIDDEN.some((name) => source.includes(name))).toBe(true);
    // And it is disarmed, not merely tolerated.
    expect(source).toMatch(/useResourceNavigation\(\)/);
  });
});

describe("the workflow graph is given a box it can resolve against", () => {
  it("declares a definite pixel height for the flow inside a pane", () => {
    /**
     * Structural, and it says so: jsdom reports every element at 0px, so "the
     * graph is blank" is not a question that can be asked here. The cause can.
     *
     * ReactFlow's own root is `height: 100%`. A percentage resolves against an
     * ancestor's *definite* height, and the chain from `.pane-chat-primitive`
     * down to the canvas runs through two auto-height elements the chat
     * stylesheet owns. Written as `height: auto; min-height: 180px` — which is
     * what this ticket's "assert no height" rule would suggest — the canvas
     * resolved to zero and the card drew an empty rectangle with ten stage
     * nodes translated outside it. Found in a browser, which is where the last
     * three tickets in this milestone found their defects too.
     *
     * The value stays under the 200px bar `containerPrimitives.smallSize`
     * enforces, so a small pane scrolls it rather than being pushed open by it.
     */
    const css = fs.readFileSync(path.resolve(__dirname, "../paneChrome.css"), "utf8");
    const declarations: Record<string, string> = {};
    for (const [, selector, body] of css
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (!/\.pane-chat-primitive\s+\.lg-primitive-workflow-flow/.test(selector)) continue;
      for (const declaration of body.split(";")) {
        const [property, ...rest] = declaration.split(":");
        if (rest.length === 0) continue;
        declarations[property.trim().toLowerCase()] = rest.join(":").trim().toLowerCase();
      }
    }
    const height = declarations["height"] ?? "(absent)";
    expect({ height, definite: /^\d+(\.\d+)?px$/.test(height) }).toEqual({
      height,
      definite: true,
    });
    expect(Number(height.replace("px", ""))).toBeLessThan(200);
  });
});
