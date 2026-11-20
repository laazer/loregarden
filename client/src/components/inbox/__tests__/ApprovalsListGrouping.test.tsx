/**
 * AC-3 / AC-5 / AC-6 / AC-7 / AC-8 / AC-11 (lg-inbox-organized-2738): the inbox
 * drawer's per-workspace headings, and the two states the rewritten branch
 * inherits with them.
 *
 * `ApprovalsList` is the shared list behind the drawer (`workspaceSlug=""`,
 * grouped) and the approvals pane (an operator-set slug, flat). Both paths are
 * exercised here, because the bug this guards against is a heading leaking into
 * the scoped surface — where it would name the one workspace the operator
 * already chose — or the grouping quietly turning the pane into the drawer.
 */

import { QueryClient } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";

import { api, type Approval } from "../../../api/client";
import { renderWithRouter } from "../../../test/renderWithRouter";
import { ApprovalsList } from "../ApprovalsList";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

const EM_DASH = "—";

function approval(
  id: string,
  workspace_slug: string,
  { gate = false }: { gate?: boolean } = {},
): Approval {
  return {
    id,
    title: id,
    level: "medium",
    workspace_slug,
    stage_key: "implement",
    stage_name: "Implement",
    impact: "",
    checklist: [],
    route_options: [],
    ticket_id: "ticket_1",
    ticket_external_id: "lg-1",
    kind: gate ? "workflow_gate" : "cli_permission",
    status: "pending",
    run_id: "",
    tool_name: "Bash",
    tool_input_json: "{}",
    // Blank on purpose: a non-empty adapter renders its own " · <adapter>
    // permission" span, which the inline-subtitle assertions would have to
    // exclude by hand.
    cli_adapter: "",
  };
}

function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

/** The group `<section>`s, in render order. */
const sections = (container: HTMLElement) =>
  Array.from(container.querySelectorAll("section.inbox-section"));

/** Spans whose whole text is the card's inline ` · <slug>` subtitle. */
const inlineSubtitles = (slug: string) =>
  Array.from(document.querySelectorAll("span")).filter(
    (node) => node.textContent === ` · ${slug}`,
  );

describe("ApprovalsList grouping", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockApi.resolveApproval.mockResolvedValue({});
  });

  // AC-11(a), AC-3, AC-5
  it("renders one section per workspace, gate-led group first, named and counted", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("lore-perm-1", "loregarden"),
      approval("lore-perm-2", "loregarden"),
      approval("blob-gate", "blobert", { gate: true }),
      approval("blob-perm", "blobert"),
    ]);

    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: newClient(),
    });

    await waitFor(() => expect(sections(container)).toHaveLength(2));

    const [first, second] = sections(container);
    expect(first.getAttribute("aria-label")).toBe(`blobert ${EM_DASH} 2 pending approvals`);
    expect(second.getAttribute("aria-label")).toBe(`loregarden ${EM_DASH} 2 pending approvals`);

    // The leading group is the one holding the gate.
    expect(first.textContent).toContain("blob-gate");

    // Heading shape: slug verbatim, never title-cased, beside its count pill.
    const heading = first.querySelector(".inbox-section-header");
    expect(heading).not.toBeNull();
    expect(heading?.querySelector("h3.inbox-section-title")?.textContent).toBe("blobert");
    expect(heading?.querySelector(".count-pill")?.textContent).toBe("2");
    expect(
      second.querySelector(".inbox-section-header .inbox-section-title")?.textContent,
    ).toBe("loregarden");

    // The pills sum to the cross-workspace total the drawer header shows.
    const pills = Array.from(container.querySelectorAll("section.inbox-section .count-pill"));
    expect(pills.reduce((total, pill) => total + Number(pill.textContent), 0)).toBe(4);

    // Accessible names are reachable as names, not only as attributes.
    expect(
      screen.getByRole("region", { name: `blobert ${EM_DASH} 2 pending approvals` }),
    ).toBe(first);
  });

  // AC-11(a), AC-4: within a group, gates stay above the prompts that arrived first.
  it("keeps the gates-first, then API order inside each group", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("blob-perm-1", "blobert"),
      approval("blob-perm-2", "blobert"),
      approval("blob-gate", "blobert", { gate: true }),
    ]);

    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: newClient(),
    });

    await waitFor(() => expect(sections(container)).toHaveLength(1));
    const titles = screen.getAllByText(/^blob-(perm-\d|gate)$/).map((node) => node.textContent);
    expect(titles).toEqual(["blob-gate", "blob-perm-1", "blob-perm-2"]);
  });

  // AC-11(b)
  it("uses the singular accessible name at exactly one pending approval", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("blob-gate", "blobert", { gate: true }),
      approval("lore-perm-1", "loregarden"),
      approval("lore-perm-2", "loregarden"),
    ]);

    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: newClient(),
    });

    await waitFor(() => expect(sections(container)).toHaveLength(2));
    const [first, second] = sections(container);
    expect(first.getAttribute("aria-label")).toBe(`blobert ${EM_DASH} 1 pending approval`);
    expect(second.getAttribute("aria-label")).toBe(`loregarden ${EM_DASH} 2 pending approvals`);
  });

  // AC-11(c)
  it("renders no heading, and the same flat list, when scoped to one workspace", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("blob-perm-1", "blobert"),
      approval("lore-perm", "loregarden"),
      approval("blob-gate", "blobert", { gate: true }),
    ]);

    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="blobert" isActive />, {
      queryClient: newClient(),
    });

    await waitFor(() => expect(screen.getByText("blob-gate")).toBeInTheDocument());

    expect(sections(container)).toHaveLength(0);
    expect(container.querySelector(".inbox-section-header")).toBeNull();
    expect(screen.queryByText("lore-perm")).not.toBeInTheDocument();
    const titles = screen.getAllByText(/^blob-(perm-1|gate)$/).map((node) => node.textContent);
    expect(titles).toEqual(["blob-gate", "blob-perm-1"]);
  });

  // AC-11(d)
  it("still renders one heading when every approval is in a single workspace", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("blob-perm-1", "blobert"),
      approval("blob-perm-2", "blobert"),
    ]);

    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: newClient(),
    });

    await waitFor(() => expect(sections(container)).toHaveLength(1));
    expect(sections(container)[0].getAttribute("aria-label")).toBe(
      `blobert ${EM_DASH} 2 pending approvals`,
    );
  });

  // AC-11(e), AC-6
  it("drops the card's inline workspace subtitle when a heading already names it", async () => {
    mockApi.approvals.mockResolvedValue([approval("blob-perm-1", "blobert")]);

    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: newClient(),
    });

    await waitFor(() => expect(sections(container)).toHaveLength(1));
    expect(screen.getByText("blob-perm-1")).toBeInTheDocument();
    expect(inlineSubtitles("blobert")).toHaveLength(0);
    // The heading is where the workspace is named now.
    expect(container.querySelector("h3.inbox-section-title")?.textContent).toBe("blobert");
  });

  // AC-11(e), AC-6: the other half — the scoped surface has no heading, so the
  // card must keep saying which workspace it belongs to.
  it("keeps the card's inline workspace subtitle on the scoped, ungrouped surface", async () => {
    mockApi.approvals.mockResolvedValue([approval("blob-perm-1", "blobert")]);

    renderWithRouter(<ApprovalsList workspaceSlug="blobert" isActive />, {
      queryClient: newClient(),
    });

    await waitFor(() => expect(screen.getByText("blob-perm-1")).toBeInTheDocument());
    expect(inlineSubtitles("blobert")).toHaveLength(1);
  });

  // AC-11(f), AC-7
  it("renders neither a heading nor the empty hint before the first response", () => {
    // Never resolves: this is the state between mount and the first payload,
    // where the old branch rendered `No pending approvals` over a pending fetch.
    mockApi.approvals.mockReturnValue(new Promise<Approval[]>(() => {}));

    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: newClient(),
    });

    expect(sections(container)).toHaveLength(0);
    expect(screen.queryByText("No pending approvals")).not.toBeInTheDocument();
    expect(container.querySelector(".inbox-empty-hint")).toBeNull();
  });

  // AC-7: a response that is genuinely empty is still the empty hint.
  it("renders the empty hint once an empty response has arrived", async () => {
    mockApi.approvals.mockResolvedValue([]);

    renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: newClient(),
    });

    expect(await screen.findByText("No pending approvals")).toBeInTheDocument();
  });

  it("names the workspace in the scoped empty hint", async () => {
    mockApi.approvals.mockResolvedValue([]);

    renderWithRouter(<ApprovalsList workspaceSlug="blobert" isActive />, {
      queryClient: newClient(),
    });

    expect(await screen.findByText("No pending approvals in blobert")).toBeInTheDocument();
  });

  // AC-11(g), AC-8
  it("reports a cold-cache failure instead of claiming nothing is pending", async () => {
    // Rejecting with a value that is neither an Error nor a string is what
    // reaches `describeError`'s fallback, which is the string AC-8 pins.
    mockApi.approvals.mockRejectedValue({ status: 500 });

    renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: newClient(),
    });

    expect(await screen.findByText("Could not load approvals")).toBeInTheDocument();
    expect(screen.queryByText("No pending approvals")).not.toBeInTheDocument();
  });

  it("prefers the failure's own words when it has any", async () => {
    mockApi.approvals.mockRejectedValue(new Error("Request failed (503)"));

    renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: newClient(),
    });

    expect(await screen.findByText("Request failed (503)")).toBeInTheDocument();
    expect(screen.queryByText("No pending approvals")).not.toBeInTheDocument();
  });

  // AC-11(h), AC-3
  it("makes the heading an h3 that nothing can tab to or click", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("blob-gate", "blobert", { gate: true }),
      approval("lore-perm", "loregarden"),
    ]);

    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: newClient(),
    });

    await waitFor(() => expect(sections(container)).toHaveLength(2));

    const headings = Array.from(container.querySelectorAll(".inbox-section-title"));
    expect(headings).toHaveLength(2);
    for (const heading of headings) {
      expect(heading.tagName).toBe("H3");
      expect(heading.getAttribute("tabindex")).toBeNull();
      expect(heading.getAttribute("role")).toBeNull();
    }

    // The header rows add no focus stop of their own, so tab order stays
    // group order then card order.
    for (const header of Array.from(container.querySelectorAll(".inbox-section-header"))) {
      expect(header.querySelectorAll("[tabindex]")).toHaveLength(0);
      expect(header.querySelectorAll("button, a, input, select, textarea")).toHaveLength(0);
    }
  });

  // AC-9: a reorder must re-key nothing, so the card element survives it.
  it("keeps a card's DOM node across a refresh that moves its group", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("blob-perm", "blobert"),
      approval("lore-perm", "loregarden"),
    ]);

    // The cache is written directly rather than waited out: the real trigger is
    // the 5s poll, and a test that sleeps for it buys nothing but wall clock.
    const client = newClient();
    const { container } = renderWithRouter(<ApprovalsList workspaceSlug="" isActive />, {
      queryClient: client,
    });

    await waitFor(() => expect(sections(container)).toHaveLength(2));
    expect(sections(container)[0].getAttribute("aria-label")).toBe(
      `blobert ${EM_DASH} 1 pending approval`,
    );
    const cardBefore = screen.getByText("lore-perm").closest(".approval-card-body");
    expect(cardBefore).not.toBeNull();

    // loregarden's prompt became a gate, so its group now leads.
    act(() => {
      client.setQueryData<Approval[]>(
        ["approvals"],
        [approval("blob-perm", "blobert"), approval("lore-perm", "loregarden", { gate: true })],
      );
    });

    await waitFor(() =>
      expect(sections(container)[0].getAttribute("aria-label")).toBe(
        `loregarden ${EM_DASH} 1 pending approval`,
      ),
    );
    expect(screen.getByText("lore-perm").closest(".approval-card-body")).toBe(cardBefore);
  });
});
