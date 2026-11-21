import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import type { Approval } from "../../api/client";
import type { ReviewItem, ReviewQueue } from "../../api/reviewApi";
import { renderWithRouter } from "../../test/renderWithRouter";
import { ReviewPage } from "../ReviewPage";

jest.mock("../../api/reviewApi", () => ({
  ...jest.requireActual("../../api/reviewApi"),
  reviewApi: {
    queue: jest.fn(),
    launchStatus: jest.fn(),
    launch: jest.fn(),
    stop: jest.fn(),
    tryRequest: jest.fn(),
  },
}));
jest.mock("../../api/localInstancesApi", () => ({
  localInstancesApi: { list: jest.fn() },
}));
jest.mock("../../api/client", () => ({
  ...jest.requireActual("../../api/client"),
  api: { resolveApproval: jest.fn(), updateTicket: jest.fn() },
}));

const { reviewApi } = jest.requireMock("../../api/reviewApi") as {
  reviewApi: Record<string, jest.Mock>;
};
const { localInstancesApi } = jest.requireMock("../../api/localInstancesApi") as {
  localInstancesApi: Record<string, jest.Mock>;
};
const { api } = jest.requireMock("../../api/client") as { api: Record<string, jest.Mock> };

function gate(n: number): Approval {
  return {
    id: `appr-${n}`,
    title: "Resolve Playtest exit actions",
    level: "medium",
    workspace_slug: "blobert",
    stage_key: "playtest",
    stage_name: "Playtest",
    impact: "Stage 'Playtest' requires human sign-off.",
    checklist: [],
    route_options: [],
    ticket_id: `t-${n}`,
    ticket_external_id: `blob-core-${n}`,
    kind: "workflow_gate",
    status: "pending",
    run_id: "",
    tool_name: "",
    tool_input_json: "{}",
    cli_adapter: "",
  };
}

function item(n: number, overrides: Partial<ReviewItem> = {}): ReviewItem {
  return {
    approval: gate(n),
    ticket: {
      id: `t-${n}`,
      external_id: `blob-core-${n}`,
      title: `Feature ${n}`,
      description: "",
      revision: 4,
      acceptance_criteria: [`criterion ${n}a`, `criterion ${n}b`],
      checked_criteria: [`criterion ${n}a`],
    },
    scenes: [`scenes/level_${n}.tscn`],
    kit: { screenshots: [], links: [], requests: [], unreadable: [] },
    ...overrides,
  };
}

const QUEUE: ReviewQueue = {
  total: 3,
  builds: [
    {
      workspace_slug: "blobert",
      ref: "integration/blob-core-131",
      combined: true,
      items: [
        item(1, {
          kit: {
            screenshots: [{ capture_id: `${"a".repeat(32)}.png`, caption: "Marker pulse" }],
            links: [{ url: "http://127.0.0.1:5173/x", label: "Sandbox page" }],
            requests: [
              { name: "List enemies", method: "GET", path: "/api/enemies", body: "", expect_status: 200 },
            ],
            unreadable: [],
          },
        }),
        item(2),
      ],
    },
    { workspace_slug: "loregarden", ref: "loregarden/lg-9", combined: false, items: [item(9)] },
  ],
};

beforeEach(() => {
  jest.clearAllMocks();
  reviewApi.queue.mockResolvedValue(QUEUE);
  reviewApi.launchStatus.mockResolvedValue({
    config: { configured: true, path: "/ws/blobert/.loregarden/review.yaml", error: "" },
    state: null,
  });
  localInstancesApi.list.mockResolvedValue({
    instances: [
      { id: "m", project: "blobert", name: "main", kind: "server", state: "ready", url: "http://127.0.0.1:8123" },
    ],
    unreadable: [],
  });
});

function renderAt(search = "") {
  return renderWithRouter(<ReviewPage />, { routerProps: { initialEntries: [`/review${search}`] } });
}

describe("ReviewPage", () => {
  it("groups gates by build and launches the combined build in one click", async () => {
    reviewApi.launch.mockResolvedValue({ phase: "preparing" });
    renderAt("?workspace=blobert");

    expect(await screen.findByRole("heading", { name: "2 tickets in one build" })).toBeInTheDocument();
    expect(reviewApi.queue).toHaveBeenCalledWith("blobert");
    const builds = screen.getByRole("list", { name: "Builds to review" });
    expect(within(builds).getAllByRole("button")).toHaveLength(2);

    expect(within(screen.getByRole("list", { name: "Tickets in this build" })).getAllByRole("button")).toHaveLength(2);

    fireEvent.click(await screen.findByRole("button", { name: "Launch build" }));

    await waitFor(() =>
      expect(reviewApi.launch).toHaveBeenCalledWith("blobert", { ref: "integration/blob-core-131", scene: "" }),
    );
  });

  it("opens a ticket's scene against the same build", async () => {
    reviewApi.launch.mockResolvedValue({ phase: "preparing" });
    renderAt();

    fireEvent.click(await screen.findByRole("button", { name: "Open scene" }));

    await waitFor(() =>
      expect(reviewApi.launch).toHaveBeenCalledWith("blobert", {
        ref: "integration/blob-core-131",
        scene: "scenes/level_1.tscn",
      }),
    );
  });

  it("shows what the agent captured: screenshot, link and an endpoint check", async () => {
    reviewApi.tryRequest.mockResolvedValue({
      ok: true,
      status: 500,
      elapsed_ms: 12,
      content_type: "application/json",
      body: '{"detail":"boom"}',
      truncated: false,
      error: "",
    });
    renderAt();

    const shot = await screen.findByRole("img", { name: "Marker pulse" });
    expect(shot.getAttribute("src")).toContain(`/api/review/captures/t-1/${"a".repeat(32)}.png`);
    expect(screen.getByRole("link", { name: "Sandbox page" })).toHaveAttribute("href", "http://127.0.0.1:5173/x");

    await waitFor(() => expect(screen.getByLabelText("Send to")).toHaveValue("http://127.0.0.1:8123"));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("500, expected 200")).toBeInTheDocument();
    expect(reviewApi.tryRequest).toHaveBeenCalledWith({
      base_url: "http://127.0.0.1:8123",
      method: "GET",
      path: "/api/enemies",
      body: "",
    });
  });

  it("saves a criterion tick with the ticket's revision", async () => {
    api.updateTicket.mockResolvedValue({});
    renderAt();

    fireEvent.click(await screen.findByLabelText("criterion 1b"));

    await waitFor(() =>
      expect(api.updateTicket).toHaveBeenCalledWith("t-1", {
        checked_acceptance_criteria: ["criterion 1a", "criterion 1b"],
        expected_revision: 4,
      }),
    );
  });

  it("walks to the next ticket", async () => {
    renderAt();

    await screen.findByRole("link", { name: "Feature 1" });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByRole("link", { name: "Feature 2" })).toBeInTheDocument();
    expect(screen.getByText("2 of 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("explains a workspace with no launch config instead of hiding the button", async () => {
    reviewApi.launchStatus.mockResolvedValue({
      config: { configured: false, path: "/ws/blobert/.loregarden/review.yaml", error: "" },
      state: null,
    });
    renderAt();

    expect(await screen.findByText(/has no launch config/)).toBeInTheDocument();
    expect(screen.getByText("/ws/blobert/.loregarden/review.yaml")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Launch build" })).not.toBeInTheDocument();
  });

  it("says nothing is waiting when the queue is empty", async () => {
    reviewApi.queue.mockResolvedValue({ total: 0, builds: [] });
    renderAt("?workspace=blobert");

    expect(await screen.findByText("Nothing is waiting on a person in blobert.")).toBeInTheDocument();
  });

  it("offers a retry when the queue fails to load", async () => {
    reviewApi.queue.mockRejectedValueOnce(new Error("down"));
    renderAt();

    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("heading", { name: "2 tickets in one build" })).toBeInTheDocument();
  });
});
