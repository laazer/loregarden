import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";

import { api } from "../../api/client";
import { ApiError } from "../../api/http";
import { mcpTelemetry, mcpToolCall as call } from "../../test/mcpFixtures";
import { McpActivityFeed } from "../mcp/McpActivityFeed";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function renderFeed(server?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <McpActivityFeed server={server} />
    </QueryClientProvider>,
  );
}

/**
 * The same feed, with its 5s background poll exposed.
 *
 * `refetchInterval: 5000` fires exactly this call when it elapses, so a test
 * that needs a second read on a surface carrying no control of its own asks for
 * one directly rather than driving jest's clock through react-query and RTL.
 */
function renderPollingFeed(server?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const result = render(
    <QueryClientProvider client={client}>
      <McpActivityFeed server={server} />
    </QueryClientProvider>,
  );
  return {
    ...result,
    poll: () =>
      act(async () => {
        await client.refetchQueries({ queryKey: ["mcp-telemetry"] });
        // query-core writes the error into the cache synchronously, but the
        // subscribed component's render lands a tick later, so a poll that
        // returned as soon as the refetch settled would let a SYNCHRONOUS
        // assertion read the pre-failure DOM and pass under an implementation
        // that never renders the notice at all. Flushing a macrotask inside
        // `act` makes the poll deterministic, which is what lets the negative
        // assertions below ("no alert", "no empty state") be trustworthy.
        await new Promise((resolve) => setTimeout(resolve, 0));
      }),
  };
}

beforeEach(() => jest.clearAllMocks());

it("explains why an empty feed may not mean no activity", async () => {
  // Bypassed runs have no permission bridge, so they record nothing. Saying
  // "no calls" without that caveat would be a misleading zero.
  mockApi.mcpTelemetry.mockResolvedValue(mcpTelemetry());

  renderFeed();
  expect(
    await screen.findByText(/runs with permissions bypassed do not appear/i),
  ).toBeInTheDocument();
});

it("counts calls per server, busiest first", async () => {
  mockApi.mcpTelemetry.mockResolvedValue(
    mcpTelemetry({ by_server: { loregarden: 2, github: 7 } }),
  );

  renderFeed();
  const names = (await screen.findAllByText(/github|loregarden/)).map((n) => n.textContent);
  expect(names[0]).toBe("github");
});

it("says how each call was resolved, in words", async () => {
  mockApi.mcpTelemetry.mockResolvedValue(
    mcpTelemetry({
      by_server: { github: 2 },
      recent: [call({ id: "a", decision: "auto_server" }), call({ id: "b", decision: "rejected" })],
    }),
  );

  renderFeed();
  expect(await screen.findByText("trusted server")).toBeInTheDocument();
  expect(screen.getByText("you rejected")).toBeInTheDocument();
});

it("shows a wait only where a human actually waited", async () => {
  mockApi.mcpTelemetry.mockResolvedValue(
    mcpTelemetry({
      recent: [
        call({ id: "a", decision: "approved", decision_ms: 90000 }),
        call({ id: "b", decision: "auto_server", decision_ms: 0 }),
      ],
    }),
  );

  renderFeed();
  // An auto-approved call has no wait worth reporting; only the human one does.
  expect(await screen.findByText(/waited 1m 30s/)).toBeInTheDocument();
  expect(screen.queryAllByText(/waited/)).toHaveLength(1);
});

it("narrows the feed to one server when asked", async () => {
  // The detail pane asks about a single server; another server's calls there
  // would read as that server's traffic.
  mockApi.mcpTelemetry.mockResolvedValue(
    mcpTelemetry({
      by_server: { github: 1, linear: 1 },
      recent: [
        call({ id: "a", server_name: "github", tool_name: "mcp__github__create_issue" }),
        call({ id: "b", server_name: "linear", tool_name: "mcp__linear__list_issues" }),
      ],
    }),
  );

  renderFeed("github");
  expect(await screen.findByText("mcp__github__create_issue")).toBeInTheDocument();
  expect(screen.queryByText("mcp__linear__list_issues")).not.toBeInTheDocument();
});

it("reports a failure rather than showing an empty feed", async () => {
  mockApi.mcpTelemetry.mockRejectedValue(new Error("boom"));

  renderFeed();
  expect(await screen.findByText(/could not load activity/i)).toBeInTheDocument();
});

/**
 * lg-milestone-that-2187 R3 (AC2, AC5, AC6, UX2) — a failed 5s poll over a feed
 * already on screen.
 *
 * The feed's error branch sits above its data render, so query-core keeping
 * `data` on a failed *refetch* means one transient failure replaced the 50
 * recent calls with "Could not load activity." and the next tick brought them
 * back. Nothing was lost, and because `queryClient.ts:28-31` suppresses the
 * query toast whenever `data !== undefined`, the inline notice is the ONLY
 * report of the failed read.
 *
 * The 5s cadence is why `role="status"` is mandatory and why the notice carries
 * no changing value: at this rate an assertive region or a counter announces to
 * a screen-reader user 720 times an hour, denying them the surface rather than
 * warning them.
 *
 * AC2's literal `telemetry.data === undefined` reading is deliberately NOT
 * implemented, because it contradicts AC2's own last sentence: a feed that read
 * zero calls and then failed has `data` defined, so the literal predicate
 * renders "No tool calls recorded yet…" over a failing read — a false health
 * claim. R3-4 is the case that separates the two readings.
 */

/** Make every telemetry read from here on reject (see failNextRead's note in the board suite). */
function failNextTelemetryRead() {
  mockApi.mcpTelemetry.mockRejectedValue(new Error(FEED_REASON));
}

const FEED_REASON = "the telemetry endpoint did not answer";
const FEED_LEAD = "This activity may be out of date — the last refresh failed.";

describe("a failed 5s poll over a feed already on screen", () => {
  it("R3-1 — keeps the recent calls and reports the failed read as one polite notice", async () => {
    mockApi.mcpTelemetry.mockResolvedValue(
      mcpTelemetry({
        by_server: { loregarden: 2 },
        recent: [
          call({ id: "a", tool_name: "mcp__loregarden__loregarden_get_ticket" }),
          call({ id: "b", tool_name: "mcp__loregarden__loregarden_update_ticket" }),
        ],
      }),
    );

    const { poll } = renderPollingFeed();
    await screen.findByText("mcp__loregarden__loregarden_get_ticket");

    failNextTelemetryRead();

    await poll();

    expect(screen.getByText("mcp__loregarden__loregarden_get_ticket")).toBeInTheDocument();
    expect(screen.getByText("mcp__loregarden__loregarden_update_ticket")).toBeInTheDocument();
    const notice = screen.getByTestId("mcp-refresh-failed");
    expect(screen.getAllByTestId("mcp-refresh-failed")).toHaveLength(1);
    expect(notice).toHaveAttribute("role", "status");
    expect(notice).toHaveTextContent(FEED_LEAD);
    expect(notice).toHaveTextContent(`${FEED_REASON}.`);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("R3-2 — never claims the feed is empty while a read is failing over calls", async () => {
    // AC2's last sentence. The empty state is a health claim about the bridge,
    // and the one thing this surface must not do is make it over a failed read.
    mockApi.mcpTelemetry.mockResolvedValue(mcpTelemetry({ recent: [call({ id: "a" })] }));

    const { poll } = renderPollingFeed();
    await screen.findByText("mcp__github__create_issue");

    failNextTelemetryRead();

    await poll();

    expect(screen.queryByText(/No tool calls recorded yet/)).toBeNull();
    expect(screen.queryByText(/runs with permissions bypassed do not appear/)).toBeNull();
  });

  it("R3-6 — leaves focus where it was across the failed poll (UX3(d))", async () => {
    mockApi.mcpTelemetry.mockResolvedValue(mcpTelemetry({ recent: [call({ id: "a" })] }));

    const { poll } = renderPollingFeed();
    await screen.findByText("mcp__github__create_issue");
    const pad = document.createElement("a");
    pad.href = "#pad";
    document.body.append(pad);
    pad.focus();
    const before = document.activeElement;

    failNextTelemetryRead();

    await poll();

    expect(screen.getByTestId("mcp-refresh-failed")).toBeInTheDocument();
    expect(document.activeElement).toBe(before);
    pad.remove();
  });

  it("UX2(e) — the notice is the first child, above the counts strip", async () => {
    mockApi.mcpTelemetry.mockResolvedValue(
      mcpTelemetry({ by_server: { loregarden: 2 }, recent: [call({ id: "a" })] }),
    );

    const { container, poll } = renderPollingFeed();
    await screen.findByText("mcp__github__create_issue");

    failNextTelemetryRead();

    await poll();

    const notice = screen.getByTestId("mcp-refresh-failed");
    const counts = container.querySelector(".mcp-activity-counts");
    expect(notice.previousElementSibling).toBeNull();
    expect(notice.compareDocumentPosition(counts!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("a feed read that had nothing to lose", () => {
  it("R3-4 — takes the full error pane when the first read succeeded with no calls", async () => {
    // The case that separates AC2's two readings. `data` is defined here, so
    // the literal `telemetry.data === undefined` predicate renders the empty
    // state over a failing read. The content-based predicate takes the pane.
    mockApi.mcpTelemetry.mockResolvedValue(mcpTelemetry());

    const { poll } = renderPollingFeed();
    await screen.findByText(/runs with permissions bypassed do not appear/);

    failNextTelemetryRead();

    await poll();

    expect(screen.getByRole("alert")).toHaveTextContent(FEED_REASON);
    expect(screen.queryByText(/No tool calls recorded yet/)).toBeNull();
    expect(screen.queryByTestId("mcp-refresh-failed")).toBeNull();
  });

  it("R3-3 — a first-load failure names the reason, not a bare 'Could not load activity.'", async () => {
    // AC2: today's copy says nothing the operator can act on. The reason comes
    // from `endSentence(describeError(error, fallback))`, so a rejection with
    // no message of its own still names the endpoint.
    mockApi.mcpTelemetry.mockRejectedValue(new ApiError(502, FEED_REASON));

    renderPollingFeed();

    const pane = await screen.findByRole("alert");
    expect(pane).toHaveTextContent("Could not load activity");
    expect(pane).toHaveTextContent(`${FEED_REASON}.`);
  });

  it("R3-3 — a first-load failure with no message falls back to naming the endpoint", async () => {
    mockApi.mcpTelemetry.mockRejectedValue(null);

    renderPollingFeed();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The telemetry endpoint did not answer.",
    );
  });

  it("R3-5 — a successful empty read is still the empty state, with no alert and no notice", async () => {
    // The empty state is legitimate and unchanged in copy; it must simply be
    // unreachable on a failure (UX2(c)).
    mockApi.mcpTelemetry.mockResolvedValue(mcpTelemetry());

    renderPollingFeed();

    await screen.findByText(/runs with permissions bypassed do not appear/);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });
});
