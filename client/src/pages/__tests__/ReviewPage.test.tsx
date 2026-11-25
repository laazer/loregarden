import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

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
    preview: jest.fn(),
    startPreview: jest.fn(),
    capture: jest.fn(),
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

function emptyKit(): ReviewItem["kit"] {
  return { scene: "", steps: [], screenshots: [], links: [], requests: [], unreadable: [] };
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
    launch: { match: "matched", targets: ["game"] },
    kit: emptyKit(),
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
            ...emptyKit(),
            screenshots: [{ capture_id: `${"a".repeat(32)}.png`, caption: "Marker pulse", scene: "scenes/level_1.tscn" }],
            links: [{ url: "http://127.0.0.1:5173/x", path: "", label: "Sandbox page" }],
            requests: [
              { name: "List enemies", method: "GET", path: "/api/enemies", body: "", expect_status: 200 },
            ],
          },
        }),
        item(2),
      ],
    },
    { workspace_slug: "loregarden", ref: "loregarden/lg-9", combined: false, items: [item(9)] },
  ],
};

const TARGETS = [
  { name: "game", kind: "app" },
  { name: "backend tests", kind: "check" },
];

const NO_PREVIEW = { ref: "integration/blob-core-131", client: null, server: null, can_start: true, unavailable: "" };

function withPages(links: ReviewItem["kit"]["links"]): ReviewQueue {
  return {
    total: 1,
    builds: [
      {
        workspace_slug: "loregarden",
        ref: "loregarden/lg-9",
        combined: false,
        items: [item(9, { scenes: [], kit: { ...emptyKit(), links } })],
      },
    ],
  };
}

const BRANCH_CLIENT = { id: "c", url: "http://127.0.0.1:5181", state: "ready" as const, worktree: "/w/lg-9" };

beforeEach(() => {
  jest.clearAllMocks();
  reviewApi.preview.mockResolvedValue(NO_PREVIEW);
  reviewApi.queue.mockResolvedValue(QUEUE);
  reviewApi.launchStatus.mockResolvedValue({
    config: { configured: true, path: "/ws/blobert/.loregarden/review.yaml", error: "", targets: TARGETS },
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

    fireEvent.click(await screen.findByRole("button", { name: "Launch game" }));

    await waitFor(() =>
      expect(reviewApi.launch).toHaveBeenCalledWith("blobert", {
        ref: "integration/blob-core-131",
        target: "game",
      }),
    );
  });

  it("lands on the gate an inbox link names, in whichever build holds it", async () => {
    renderAt("?approval=appr-2");

    const tickets = await screen.findByRole("list", { name: "Tickets in this build" });
    const current = within(tickets).getByRole("button", { current: "step" });
    expect(current).toHaveTextContent("blob-core-2");
    // The card on the review page does not offer the review page again.
    expect(screen.queryByRole("button", { name: "Playtest" })).not.toBeInTheDocument();
  });

  it("falls back to the first gate when the linked one is already resolved", async () => {
    renderAt("?approval=appr-gone");

    const tickets = await screen.findByRole("list", { name: "Tickets in this build" });
    expect(within(tickets).getByRole("button", { current: "step" })).toHaveTextContent("blob-core-1");
  });

  it("plays the only scene a ticket changes, against the same build", async () => {
    reviewApi.launch.mockResolvedValue({ phase: "preparing" });
    renderAt();

    fireEvent.click(await screen.findByRole("button", { name: "Play level_1" }));

    await waitFor(() =>
      expect(reviewApi.launch).toHaveBeenCalledWith("blobert", {
        ref: "integration/blob-core-131",
        scene: "scenes/level_1.tscn",
      }),
    );
  });

  describe("what to do", () => {
    function oneItem(overrides: Partial<ReviewItem>): ReviewQueue {
      return {
        total: 1,
        builds: [
          { workspace_slug: "blobert", ref: "integration/blob-core-131", combined: true, items: [item(1, overrides)] },
        ],
      };
    }
    const STEPS = [
      { action: "Press C on the enemy's left side", expect: "A pale blue flash", wrong_if: "No flash" },
      { action: "Press C far away", expect: "A low cue and no flash", wrong_if: "" },
    ];

    it("lists the steps and plays the scene they happen in, which the ticket need not change", async () => {
      reviewApi.launch.mockResolvedValue({ phase: "preparing" });
      reviewApi.queue.mockResolvedValue(
        oneItem({ scenes: [], kit: { ...emptyKit(), scene: "scenes/sandbox/latch_3d.tscn", steps: STEPS } }),
      );
      renderAt();

      const guide = await screen.findByRole("region", { name: "What to do" });
      const steps = within(guide).getAllByRole("listitem");
      expect(steps).toHaveLength(2);
      expect(steps[0]).toHaveTextContent("Press C on the enemy's left side");
      expect(steps[0]).toHaveTextContent("A pale blue flash");
      expect(steps[0]).toHaveTextContent("Wrong if No flash");
      expect(steps[1]).not.toHaveTextContent("Wrong if");

      fireEvent.click(await within(guide).findByRole("button", { name: "Play latch_3d" }));
      await waitFor(() =>
        expect(reviewApi.launch).toHaveBeenCalledWith("blobert", {
          ref: "integration/blob-core-131",
          scene: "scenes/sandbox/latch_3d.tscn",
        }),
      );
      // The target's own button starts somewhere else, so it says so and is not the primary one.
      expect(await screen.findByText(/Launching starts from the beginning/)).toHaveTextContent("use Play under What to do");
      expect(screen.getByRole("button", { name: "Launch game" })).toHaveClass("btn-secondary");
    });

    it("does not say nothing runs a ticket that has a scene to play", async () => {
      reviewApi.queue.mockResolvedValue(
        oneItem({
          scenes: [],
          launch: { match: "no_match", targets: [] },
          kit: { ...emptyKit(), scene: "scenes/sandbox/latch_3d.tscn", steps: STEPS },
        }),
      );
      renderAt();

      expect(await screen.findByRole("button", { name: "Play latch_3d" })).toBeInTheDocument();
      expect(await screen.findByText(/checked in its own scene/)).not.toHaveTextContent("Launching starts from the beginning");
      expect(screen.queryByText(/can launch runs the files/)).not.toBeInTheDocument();
    });

    it("folds the criteria away and drops the generated checklist once there are steps", async () => {
      const checklist = ["Open `scenes/level_1.tscn` in the editor"];
      reviewApi.queue.mockResolvedValue(
        oneItem({ approval: { ...gate(1), checklist }, kit: { ...emptyKit(), steps: STEPS } }),
      );
      renderAt();

      await screen.findByRole("region", { name: "What to do" });
      expect(screen.getByText("Acceptance criteria (1/2 ticked)").closest("details")).not.toHaveAttribute("open");
      expect(screen.queryByText(/Open `scenes\/level_1/)).not.toBeInTheDocument();
    });

    it("says nobody wrote steps, and keeps the criteria and checklist open", async () => {
      const checklist = ["Open `scenes/level_1.tscn` in the editor"];
      reviewApi.queue.mockResolvedValue(oneItem({ approval: { ...gate(1), checklist } }));
      renderAt();

      const guide = await screen.findByRole("region", { name: "What to do" });
      expect(guide).toHaveTextContent("No steps were written for this ticket");
      expect(screen.getByText("Acceptance criteria (1/2 ticked)").closest("details")).toHaveAttribute("open");
      expect(screen.getByText(/Open `scenes\/level_1/)).toBeInTheDocument();
    });

    it("sends the reviewer to the target's button when the steps have no one scene", async () => {
      reviewApi.launch.mockResolvedValue({ phase: "preparing" });
      reviewApi.queue.mockResolvedValue(
        oneItem({ scenes: ["scenes/a.tscn", "scenes/b.tscn"], kit: { ...emptyKit(), steps: STEPS } }),
      );
      renderAt();

      const guide = await screen.findByRole("region", { name: "What to do" });
      expect(guide).toHaveTextContent("Start it with the button at the top of this build");
      const launch = await screen.findByRole("button", { name: "Launch game" });
      expect(launch).not.toHaveClass("btn-secondary");
      fireEvent.click(launch);
      await waitFor(() =>
        expect(reviewApi.launch).toHaveBeenCalledWith("blobert", { ref: "integration/blob-core-131", target: "game" }),
      );
      // Several changed scenes are a choice, so Play does not guess one.
      expect(screen.queryByRole("button", { name: /^Play/ })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Open scene scenes/b.tscn" }));
      await waitFor(() =>
        expect(reviewApi.launch).toHaveBeenCalledWith("blobert", {
          ref: "integration/blob-core-131",
          scene: "scenes/b.tscn",
        }),
      );
      expect(screen.queryByText(/Launching starts from the beginning/)).not.toBeInTheDocument();
    });
  });

  it("names the scene a screenshot shows", async () => {
    renderAt();

    const shot = (await screen.findByRole("img", { name: "Marker pulse" })).closest("li");
    expect(shot).toHaveTextContent("Scene: level_1");
  });

  it("says a screenshot's file did not load, and lets the reviewer try it again", async () => {
    renderAt();

    fireEvent.error(await screen.findByRole("img", { name: "Marker pulse" }));

    expect(screen.queryByRole("img", { name: "Marker pulse" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open full size: Marker pulse" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/did not load/);
    expect(screen.getByText("Marker pulse")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.getByRole("img", { name: "Marker pulse" })).toBeInTheDocument();
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
      config: { configured: false, path: "/ws/blobert/.loregarden/review.yaml", error: "", targets: [] },
      state: null,
    });
    renderAt();

    expect(await screen.findByText(/has no launch config/)).toBeInTheDocument();
    expect(screen.getByText("/ws/blobert/.loregarden/review.yaml")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Launch game" })).not.toBeInTheDocument();
  });

  describe("what a ticket can launch", () => {
    const config = { configured: true, path: "/ws/blobert/.loregarden/review.yaml", error: "", targets: TARGETS };

    function withLaunch(launch: ReviewItem["launch"]): ReviewQueue {
      return {
        total: 1,
        builds: [
          { workspace_slug: "blobert", ref: "loregarden/blob-181", combined: false, items: [item(181, { scenes: [], launch })] },
        ],
      };
    }

    function ran(overrides: Record<string, unknown>) {
      reviewApi.launchStatus.mockResolvedValue({
        config,
        state: {
          workspace_slug: "blobert",
          ref: "loregarden/blob-181",
          sha: "e53cb38f97fc",
          target: "backend tests",
          kind: "check",
          scene: "",
          worktree: "/w",
          phase: "exited",
          pid: 1,
          exit_code: 0,
          error: "",
          log_tail: [],
          started_at: "",
          ...overrides,
        },
      });
    }

    it("offers only the target that runs its changes", async () => {
      reviewApi.queue.mockResolvedValue(withLaunch({ match: "matched", targets: ["backend tests"] }));
      reviewApi.launch.mockResolvedValue({ phase: "running" });
      renderAt();

      fireEvent.click(await screen.findByRole("button", { name: "Run backend tests" }));

      await waitFor(() =>
        expect(reviewApi.launch).toHaveBeenCalledWith("blobert", {
          ref: "loregarden/blob-181",
          target: "backend tests",
        }),
      );
      expect(screen.queryByRole("button", { name: "Launch game" })).not.toBeInTheDocument();
    });

    it("changes with the ticket inside one build", async () => {
      reviewApi.queue.mockResolvedValue({
        total: 2,
        builds: [
          {
            workspace_slug: "blobert",
            ref: "integration/x",
            combined: true,
            items: [item(1), item(2, { launch: { match: "matched", targets: ["backend tests"] } })],
          },
        ],
      });
      renderAt();

      expect(await screen.findByRole("button", { name: "Launch game" })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Next" }));

      expect(await screen.findByRole("button", { name: "Run backend tests" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Launch game" })).not.toBeInTheDocument();
    });

    it("links the diff when nothing runs its changes", async () => {
      reviewApi.queue.mockResolvedValue(withLaunch({ match: "no_match", targets: [] }));
      renderAt();

      expect(await screen.findByRole("link", { name: "Read its diff" })).toHaveAttribute("href", "/tickets/t-181/diff");
      expect(screen.queryByRole("button", { name: /^(Launch|Run) / })).not.toBeInTheDocument();
    });

    it.each([
      ["its changes could not be read", { match: "unknown", targets: ["game", "backend tests"] }],
      ["the config gained targets since the queue was read", { match: "unconfigured", targets: [] }],
      ["the config dropped the matched target", { match: "matched", targets: ["gone"] }],
    ] as const)("lists every target when %s", async (_why, launch) => {
      reviewApi.queue.mockResolvedValue(withLaunch({ match: launch.match, targets: [...launch.targets] }));
      renderAt();

      expect(await screen.findByRole("button", { name: "Launch game" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Run backend tests" })).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Read its diff" })).not.toBeInTheDocument();
    });

    it("calls a check that exits a pass, and an app that exits just exited", async () => {
      reviewApi.queue.mockResolvedValue(withLaunch({ match: "matched", targets: ["backend tests"] }));
      ran({});
      const { unmount } = renderAt();
      expect(await screen.findByText("Passed")).toBeInTheDocument();
      unmount();

      ran({ target: "game", kind: "app" });
      renderAt();
      expect(await screen.findByText("Exited")).toBeInTheDocument();
    });

    it("does not show another build's error under this ticket", async () => {
      reviewApi.queue.mockResolvedValue(withLaunch({ match: "matched", targets: ["backend tests"] }));
      ran({ ref: "loregarden/other", phase: "failed", error: "other build broke", log_tail: ["boom"] });
      renderAt();

      expect(await screen.findByText(/loregarden\/other/)).toBeInTheDocument();
      expect(screen.queryByText("other build broke")).not.toBeInTheDocument();
      expect(screen.queryByText("boom")).not.toBeInTheDocument();
    });
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

  describe("pages to check", () => {
    const PAGES = [
      { url: "", path: "/work-items", label: "Work items" },
      { url: "", path: "/review", label: "" },
    ];

    it("opens a path on the instance running the build and captures every page", async () => {
      reviewApi.queue.mockResolvedValue(withPages(PAGES));
      reviewApi.preview.mockResolvedValue({ ...NO_PREVIEW, ref: "loregarden/lg-9", client: BRANCH_CLIENT });
      reviewApi.capture.mockResolvedValue({
        base_url: "http://127.0.0.1:5181",
        artifact_id: "a1",
        pages: [
          { path: "/work-items", captured: true, note: "" },
          { path: "/review", captured: false, note: "net::ERR_CONNECTION_REFUSED" },
        ],
      });
      renderAt();

      expect(await screen.findByRole("link", { name: "Work items" })).toHaveAttribute(
        "href",
        "http://127.0.0.1:5181/work-items",
      );
      expect(reviewApi.preview).toHaveBeenCalledWith("loregarden", "loregarden/lg-9");
      fireEvent.click(screen.getByRole("button", { name: "Capture 2 page(s)" }));

      await waitFor(() =>
        expect(reviewApi.capture).toHaveBeenCalledWith("loregarden", {
          ref: "loregarden/lg-9",
          ticket_id: "t-9",
          paths: ["/work-items", "/review"],
        }),
      );
      expect(await screen.findByText(/1 of 2 captured/)).toBeInTheDocument();
      expect(screen.getByText(/not captured: net::ERR_CONNECTION_REFUSED/)).toBeInTheDocument();
    });

    it("offers Start preview when nothing runs the build, and does not link to a dead page", async () => {
      reviewApi.queue.mockResolvedValue(withPages(PAGES));
      reviewApi.startPreview.mockResolvedValue({
        ...NO_PREVIEW,
        ref: "loregarden/lg-9",
        client: { ...BRANCH_CLIENT, state: "starting" },
      });
      renderAt();

      fireEvent.click(await screen.findByRole("button", { name: "Start preview" }));

      await waitFor(() => expect(reviewApi.startPreview).toHaveBeenCalledWith("loregarden", "loregarden/lg-9"));
      expect(await screen.findByText(/is starting; the links open once it is ready/)).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Work items" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Capture 2 page(s)" })).toBeDisabled();
    });

    it("says where to start one when this workspace has no preview templates", async () => {
      reviewApi.queue.mockResolvedValue(withPages(PAGES));
      reviewApi.preview.mockResolvedValue({
        ...NO_PREVIEW,
        can_start: false,
        unavailable: "start blobert's instance for this branch on the Instances page.",
      });
      renderAt();

      expect(await screen.findByText(/on the Instances page/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Start preview" })).not.toBeInTheDocument();
    });

    it("shows a failed instance check with a retry, not an empty section", async () => {
      reviewApi.queue.mockResolvedValue(withPages(PAGES));
      reviewApi.preview.mockRejectedValueOnce(new Error("down"));
      renderAt();

      expect(await screen.findByText(/Could not check which instance runs/)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(await screen.findByRole("button", { name: "Start preview" })).toBeInTheDocument();
    });
  });
});
