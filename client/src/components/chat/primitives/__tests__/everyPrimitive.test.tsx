import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { PRIMITIVE_RENDERERS, renderChatPart } from "../registry";
import type { ChatPart, PrimitiveKind } from "../types";

/**
 * The least each kind needs to be a card — the client half of
 * `_MINIMAL_FENCES` in server/tests/test_chat_primitives.py. Typed as a full
 * record, so a kind added to `PrimitiveKind` without a case here fails `tsc`.
 */
const MINIMAL_PARTS: { [K in PrimitiveKind]: Extract<ChatPart, { primitive: K }> } = {
  text: { primitive: "text", content: "hi" },
  thinking: { primitive: "thinking", content: "hmm" },
  ticket: { primitive: "ticket", ticket_id: "t1" },
  ticket_workflow: { primitive: "ticket_workflow", ticket_id: "t1" },
  parent_ticket: { primitive: "parent_ticket", ticket_id: "t1" },
  ticket_list: { primitive: "ticket_list", ticket_ids: ["t1"] },
  status_column: { primitive: "status_column", status: "backlog" },
  kanban: { primitive: "kanban", statuses: ["backlog"] },
  filterable_kanban: { primitive: "filterable_kanban", filters: ["mine"] },
  agent: { primitive: "agent", slug: "planner" },
  workflow: { primitive: "workflow", workflow_slug: "default" },
  gate: { primitive: "gate", ticket_id: "t1", stage_key: "verify" },
  terminal: { primitive: "terminal", lines: [{ kind: "command", text: "ls" }] },
  edit: { primitive: "edit", content: "x" },
  calendar: {
    primitive: "calendar",
    events: [{ title: "Run", starts_at: "2026-10-21T09:00:00Z" }],
  },
  calendar_event: {
    primitive: "calendar_event",
    event: { title: "Run", starts_at: "2026-10-21T09:00:00Z" },
  },
  workspace: { primitive: "workspace", workspace_slug: "loregarden" },
  todo_list: { primitive: "todo_list", items: [{ id: "a", text: "Do it" }] },
  branch_history: { primitive: "branch_history", workspace_slug: "loregarden", branch: "main" },
  commit: { primitive: "commit", workspace_slug: "loregarden", sha: "HEAD" },
  qa: { primitive: "qa", items: [{ id: "q", question: "Which?" }] },
  btw: { primitive: "btw", exchange_id: "e1", ticket_id: "t1", question: "Why?" },
  giphy: { primitive: "giphy", giphy_id: "abc" },
};

function renderPart(part: ChatPart) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{renderChatPart(part, "part")}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("every chat primitive", () => {
  let consoleError: jest.SpyInstance;

  beforeEach(() => {
    // Every live lookup stays in flight: the card must stand on the part alone.
    globalThis.fetch = jest.fn(() => new Promise<Response>(() => {}));
    consoleError = jest.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleError.mockRestore();
  });

  it("has a renderer for every kind", () => {
    expect(Object.keys(PRIMITIVE_RENDERERS).sort()).toEqual(Object.keys(MINIMAL_PARTS).sort());
  });

  it.each(Object.values(MINIMAL_PARTS).map((part) => [part.primitive, part] as const))(
    "renders %s from its minimal part",
    (_kind, part) => {
      const { container } = renderPart(part);

      expect(container.textContent).not.toMatch(/Unknown primitive/);
      expect(container.textContent?.trim()).not.toBe("");
      expect(consoleError).not.toHaveBeenCalled();
    },
  );
});
