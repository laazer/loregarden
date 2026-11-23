import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import type {
  ChatFocus,
  FeatureLoop,
  FeatureLoopCapacity,
} from "../../../api/chatWorkbenchTypes";
import { ApiError, api } from "../../../api/client";
import { ChangesCard } from "../workbench/ChangesCard";
import { FeatureCard } from "../workbench/FeatureCard";
import { ScopeCard } from "../workbench/ScopeCard";

jest.mock("../../../api/client", () => {
  const actual = jest.requireActual("../../../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      workspaces: jest.fn(),
      initiatives: jest.fn(),
      tickets: jest.fn(),
    },
  };
});

const mockedApi = api as jest.Mocked<typeof api>;

function wrap(children: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const FOCUS: ChatFocus = {
  all_workspaces: false,
  workspaces: [
    { id: "w1", slug: "loregarden", fixed: true },
    { id: "w2", slug: "blobert", fixed: false },
  ],
  initiatives: [
    {
      id: "i1",
      external_id: "init-1",
      title: "Cross-repo launch",
      fixed: false,
    },
  ],
};

/** Room to spare: 1 on the ticket, nothing queued, cap 12. */
const ROOM: FeatureLoopCapacity = {
  on_ticket: 1,
  queued: 0,
  max: 12,
  at_cap: false,
  message: null,
};

/**
 * The cap reached, with the server's own refusal wording carried on the view.
 *
 * The string is the fixture's, never a copy typed into an assertion: the words
 * live in `chat_feature_loop._capacity_message` and a second copy here is the
 * drift this ticket exists to prevent.
 */
const AT_CAP: FeatureLoopCapacity = {
  on_ticket: 11,
  queued: 1,
  max: 12,
  at_cap: true,
  message:
    "The ticket holds at most 12 criteria: it has 11, and 1 are queued. " +
    "Remove criteria from the ticket, or finish this feature and start a new loop for the rest.",
};

function loop(overrides: Partial<FeatureLoop> = {}): FeatureLoop {
  return {
    ticket: {
      id: "t1",
      external_id: "lg-dark-1",
      title: "Dark mode",
      state: "in_progress",
      stage_key: "implement",
      workspace_slug: "loregarden",
      criteria_count: 1,
    },
    status: "running",
    reason: "",
    round: 1,
    max_criteria: 12,
    capacity: ROOM,
    criteria: [
      { id: "c1", text: "Toggle persists", status: "in_round", round: 1 },
      {
        id: "c2",
        text: "Respects the OS setting",
        status: "proposed",
        round: 0,
      },
      { id: "c3", text: "Contrast passes AA", status: "proposed", round: 0 },
    ],
    runs: [],
    ...overrides,
  };
}

/** Two proposals, so `Accept all` renders: it is hidden for a single row. */
function atCap(overrides: Partial<FeatureLoop> = {}): FeatureLoop {
  return loop({
    ticket: { ...loop().ticket, criteria_count: 11 },
    capacity: AT_CAP,
    ...overrides,
  });
}

beforeEach(() => {
  mockedApi.workspaces.mockResolvedValue([
    { slug: "loregarden", archived_at: null },
    { slug: "blobert", archived_at: null },
    { slug: "lore-eden", archived_at: null },
  ] as never);
  mockedApi.initiatives.mockResolvedValue([
    {
      id: "i1",
      external_id: "init-1",
      title: "Cross-repo launch",
      state: "in_progress",
    },
    { id: "i2", external_id: "init-2", title: "Second push", state: "backlog" },
  ] as never);
  mockedApi.tickets.mockResolvedValue([
    { id: "m1", external_id: "lg-ms-9", title: "Q4 polish" },
  ] as never);
});

describe("ScopeCard", () => {
  it("names every workspace and initiative in scope, and where turns run", () => {
    wrap(
      <ScopeCard
        focus={FOCUS}
        isGlobal={false}
        hostSlug="loregarden"
        saving={false}
        onSave={jest.fn()}
      />,
    );

    const workspaces = screen.getByRole("list", {
      name: "Workspaces in scope",
    });
    expect(
      within(workspaces)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual(["▤loregardenruns here", "▤blobert"]);
    expect(
      screen.getByRole("button", { name: "Cross-repo launch" }),
    ).toBeInTheDocument();
  });

  it("edits the focus in a modal: several of each, saved as one replacement", async () => {
    const onSave = jest.fn().mockResolvedValue(undefined);
    wrap(
      <ScopeCard
        focus={FOCUS}
        isGlobal={false}
        hostSlug="loregarden"
        saving={false}
        onSave={onSave}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(
      await within(dialog).findByRole("checkbox", { name: /lore-eden/ }),
    );
    fireEvent.click(
      await within(dialog).findByRole("checkbox", { name: /Second push/ }),
    );
    expect(
      within(dialog).getByRole("checkbox", { name: /loregarden/ }),
    ).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Focus on 5" }));

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith({
        workspace_slugs: ["blobert", "lore-eden"],
        initiative_ids: ["i1", "i2"],
      }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("keeps the dialog and says why when the focus is refused", async () => {
    const onSave = jest
      .fn()
      .mockRejectedValue(new ApiError(400, "No workspace named ghost"));
    wrap(
      <ScopeCard
        focus={FOCUS}
        isGlobal={false}
        hostSlug="loregarden"
        saving={false}
        onSave={onSave}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Focus on 3" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "No workspace named ghost",
    );
  });
});

describe("FeatureCard", () => {
  const handlers = () => ({
    onStart: jest.fn().mockResolvedValue(undefined),
    onDecide: jest.fn(),
    onAdd: jest.fn().mockResolvedValue(undefined),
  });

  it("without a loop, starts one in a modal under a picked milestone", async () => {
    const h = handlers();
    wrap(
      <FeatureCard
        feature={null}
        workspaces={["loregarden", "blobert"]}
        defaultWorkspace="loregarden"
        busy={false}
        starting={false}
        {...h}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Start a feature loop…" }),
    );
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Title"), {
      target: { value: "Dark mode" },
    });
    await within(dialog).findByRole("option", { name: "lg-ms-9 · Q4 polish" });
    fireEvent.change(within(dialog).getByLabelText("Under milestone"), {
      target: { value: "m1" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Create and start loop" }),
    );

    await waitFor(() =>
      expect(h.onStart).toHaveBeenCalledWith({
        title: "Dark mode",
        workspace_slug: "loregarden",
        parent_ticket_id: "m1",
      }),
    );
  });

  it("puts proposals waiting on the operator first, each with Accept and Drop", () => {
    const h = handlers();
    wrap(
      <FeatureCard
        feature={loop()}
        workspaces={[]}
        defaultWorkspace=""
        busy={false}
        starting={false}
        {...h}
      />,
    );

    const groups = screen
      .getAllByRole("heading", { level: 4 })
      .map((el) => el.textContent);
    expect(groups).toEqual(["Proposed — waiting on you 2", "In this round 1"]);
    fireEvent.click(
      screen.getByRole("button", { name: "Accept: Respects the OS setting" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Drop: Contrast passes AA" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Accept all" }));

    expect(h.onDecide.mock.calls).toEqual([
      [["c2"], true],
      [["c3"], false],
      [["c2", "c3"], true],
    ]);
  });

  it("queues a criterion the operator types, and clears the field once it lands", async () => {
    const h = handlers();
    wrap(
      <FeatureCard
        feature={loop()}
        workspaces={[]}
        defaultWorkspace=""
        busy={false}
        starting={false}
        {...h}
      />,
    );

    const field = screen.getByRole("textbox", {
      name: "Add an acceptance criterion",
    });
    fireEvent.change(field, { target: { value: "Works offline" } });
    fireEvent.click(screen.getByRole("button", { name: "Queue" }));

    expect(h.onAdd).toHaveBeenCalledWith("Works offline");
    await waitFor(() => expect(field).toHaveValue(""));
  });

  it("disables every decision while one is in flight", () => {
    wrap(
      <FeatureCard
        feature={loop()}
        workspaces={[]}
        defaultWorkspace=""
        busy
        starting={false}
        {...handlers()}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Accept: Respects the OS setting" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Accept all" })).toBeDisabled();
  });

  it("says why a stalled loop is not moving", () => {
    wrap(
      <FeatureCard
        feature={loop({ status: "blocked", reason: "Gate failed: lint" })}
        workspaces={[]}
        defaultWorkspace=""
        busy={false}
        starting={false}
        {...handlers()}
      />,
    );

    expect(screen.getByText("round 1 · stalled")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Gate failed: lint");
  });

  // AC 6. `variant="plain"` plus `.chat-side-card-link` is `border: none;
  // background: none; padding: 0` — link text sitting inches above the real
  // bordered Accept/Drop buttons in the rows beneath it.
  it("gives Accept all the same visible affordance as the Accept beneath it", () => {
    wrap(
      <FeatureCard
        feature={loop()}
        workspaces={[]}
        defaultWorkspace=""
        busy={false}
        starting={false}
        {...handlers()}
      />,
    );

    const acceptAll = screen.getByRole("button", { name: "Accept all" });
    const rowAccept = screen.getByRole("button", {
      name: "Accept: Respects the OS setting",
    });
    expect(acceptAll).not.toHaveClass("chat-side-card-link");
    expect(acceptAll).toHaveClass("btn-secondary");
    expect(rowAccept).toHaveClass("btn-secondary");
  });

  describe("at the cap", () => {
    it("says so on the card, above every control it disables", () => {
      const { container } = wrap(
        <FeatureCard
          feature={atCap()}
          workspaces={[]}
          defaultWorkspace=""
          busy={false}
          starting={false}
          {...handlers()}
        />,
      );

      // The words are the server's, read off the fixture: a copy typed here
      // would pass while the server's own refusal said something else.
      const message = screen.getByText(AT_CAP.message as string);
      expect(message).toHaveClass("chat-wb-warn");
      expect(message).toHaveAttribute("role", "status");

      // Reading order, not just presence: a screen reader going through the
      // card must meet the reason before the dead buttons, and the operator
      // must not have to scroll 30 rows to find it.
      const firstGroup = screen.getAllByRole("heading", { level: 4 })[0];
      const form = container.querySelector("form.chat-wb-add");
      expect(form).not.toBeNull();
      const precedes = (a: Element, b: Element) =>
        Boolean(
          a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING,
        );
      expect(precedes(message, firstGroup)).toBe(true);
      expect(precedes(message, form as Element)).toBe(true);
    });

    it("disables every write control with the HTML attribute, not a style", () => {
      wrap(
        <FeatureCard
          feature={atCap()}
          workspaces={[]}
          defaultWorkspace=""
          busy={false}
          starting={false}
          {...handlers()}
        />,
      );

      const dead = [
        screen.getByRole("button", { name: "Accept all" }),
        screen.getByRole("button", { name: "Accept: Respects the OS setting" }),
        screen.getByRole("button", { name: "Accept: Contrast passes AA" }),
        screen.getByRole("textbox", { name: "Add an acceptance criterion" }),
        screen.getByRole("button", { name: "Queue" }),
      ];
      for (const control of dead) {
        expect(control).toBeDisabled();
        // aria-disabled plus CSS keeps a dead control in the tab order and
        // still clickable; the repo's position on that is recorded in
        // WorkflowMonitorView.test.tsx.
        expect(control).not.toHaveAttribute("aria-disabled");
      }
    });

    // A drop is never refused by _check_capacity, and dropping is the way out.
    it("leaves Drop alive, because it is the way out", () => {
      const h = handlers();
      wrap(
        <FeatureCard
          feature={atCap()}
          workspaces={[]}
          defaultWorkspace=""
          busy={false}
          starting={false}
          {...h}
        />,
      );

      const drop = screen.getByRole("button", {
        name: "Drop: Respects the OS setting",
      });
      expect(drop).toBeEnabled();
      fireEvent.click(drop);
      expect(h.onDecide).toHaveBeenCalledWith(["c2"], false);
    });

    // Disabling the Queue button is not enough: Enter in the field submits the
    // form without going near the button, and the submit handler guarded only
    // on `busy`.
    it("fires nothing when the add form is submitted from the keyboard", () => {
      const h = handlers();
      const { container } = wrap(
        <FeatureCard
          feature={atCap()}
          workspaces={[]}
          defaultWorkspace=""
          busy={false}
          starting={false}
          {...h}
        />,
      );

      const field = screen.getByRole("textbox", {
        name: "Add an acceptance criterion",
      });
      fireEvent.change(field, { target: { value: "Works offline" } });
      fireEvent.submit(container.querySelector("form.chat-wb-add") as Element);

      expect(h.onAdd).not.toHaveBeenCalled();
      expect(h.onDecide).not.toHaveBeenCalled();
    });

    // The add path calls the same _check_capacity with adding=1, so the message
    // is about the ticket's room and not about the proposals on screen.
    it("still says so with no proposals left to accept", () => {
      wrap(
        <FeatureCard
          feature={atCap({
            criteria: [
              { id: "c1", text: "Toggle persists", status: "met", round: 1 },
            ],
          })}
          workspaces={[]}
          defaultWorkspace=""
          busy={false}
          starting={false}
          {...handlers()}
        />,
      );

      expect(screen.getByText(AT_CAP.message as string)).toBeInTheDocument();
      expect(
        screen.getByRole("textbox", { name: "Add an acceptance criterion" }),
      ).toBeDisabled();
      expect(screen.getByRole("button", { name: "Queue" })).toBeDisabled();
    });

    // `at_cap` is computed on the server with the growth guard's own predicate,
    // which disagrees with `on_ticket + queued >= max` at (cap+1, cap+2). A card
    // that re-derived it from the counts would pass every test above and this
    // one would red: here the counts say there is room and the server says
    // there is not.
    it("obeys the server's at_cap rather than counting the criteria itself", () => {
      const feature = loop({
        capacity: {
          on_ticket: 2,
          queued: 0,
          max: 12,
          at_cap: true,
          message: AT_CAP.message,
        },
      });
      wrap(
        <FeatureCard
          feature={feature}
          workspaces={[]}
          defaultWorkspace=""
          busy={false}
          starting={false}
          {...handlers()}
        />,
      );

      expect(screen.getByText(AT_CAP.message as string)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Accept all" })).toBeDisabled();
    });

    // One wording, built once. A card that composed its own sentence from the
    // counts would read plausibly and drift the moment the server's changed.
    it("renders the string it was given and composes none of its own", () => {
      const message = "Nothing doing: 7 on the ticket, 5 queued, cap 12.";
      wrap(
        <FeatureCard
          feature={loop({
            capacity: {
              on_ticket: 7,
              queued: 5,
              max: 12,
              at_cap: true,
              message,
            },
          })}
          workspaces={[]}
          defaultWorkspace=""
          busy={false}
          starting={false}
          {...handlers()}
        />,
      );

      const warn = screen.getByText(message);
      expect(warn.textContent).toBe(message);
    });

    // `busy || at_cap`, not `busy && at_cap`: either alone disables, and the cap
    // message must not appear or vanish because a click is in flight.
    it("disables independently of the in-flight flag, and keeps saying why", () => {
      wrap(
        <FeatureCard
          feature={atCap()}
          workspaces={[]}
          defaultWorkspace=""
          busy
          starting={false}
          {...handlers()}
        />,
      );

      expect(screen.getByRole("button", { name: "Accept all" })).toBeDisabled();
      expect(screen.getByText(AT_CAP.message as string)).toBeInTheDocument();
    });
  });

  describe("under the cap", () => {
    it("keeps every write control alive", () => {
      const h = handlers();
      wrap(
        <FeatureCard
          feature={loop()}
          workspaces={[]}
          defaultWorkspace=""
          busy={false}
          starting={false}
          {...h}
        />,
      );

      expect(screen.getByRole("button", { name: "Accept all" })).toBeEnabled();
      expect(
        screen.getByRole("button", { name: "Accept: Respects the OS setting" }),
      ).toBeEnabled();
      expect(
        screen.getByRole("textbox", { name: "Add an acceptance criterion" }),
      ).toBeEnabled();
      fireEvent.click(screen.getByRole("button", { name: "Accept all" }));
      expect(h.onDecide).toHaveBeenCalledWith(["c2", "c3"], true);
    });

    // The message is the only thing the capacity payload adds to the card under
    // the cap, and it must add nothing: `.chat-wb-warn` is also `feature.reason`'s
    // class, so a message that leaked through at `at_cap: false` would read as a
    // stalled loop.
    it("says nothing about capacity", () => {
      wrap(
        <FeatureCard
          feature={loop()}
          workspaces={[]}
          defaultWorkspace=""
          busy={false}
          starting={false}
          {...handlers()}
        />,
      );

      expect(screen.queryByText(/holds at most/)).toBeNull();
      expect(screen.queryByRole("status")).toBeNull();
    });
  });

  // Criterion 12. At the cap the server hands the card the same sentence twice:
  // `_assess_capacity`'s reason and `capacity.message` are one helper's output
  // over one set of counts, so both render and the card stutters — two
  // `.chat-wb-warn` paragraphs with two different next actions, which is what
  // lg-baxter-chat-2770 filed. The dedupe is string equality, so a reason that
  // is *not* the cap sentence keeps its own paragraph.
  describe("the cap paragraph, at the live loop's shape", () => {
    // 12 on the ticket, 11 queued, 18 proposed: lg-inbox-organized-2738, the one
    // live loop, measured. The string is the server's own wording; a second copy
    // typed into an assertion is the drift this ticket exists to prevent.
    const CAP_MESSAGE =
      "The ticket holds at most 12 criteria: it has 12, and 11 are queued. " +
      "Remove criteria from the ticket, or finish this feature and start a new loop for the rest.";

    const liveShape = (overrides: Partial<FeatureLoop> = {}): FeatureLoop =>
      loop({
        ticket: { ...loop().ticket, criteria_count: 12 },
        status: "blocked",
        reason: CAP_MESSAGE,
        capacity: {
          on_ticket: 12,
          queued: 11,
          max: 12,
          at_cap: true,
          message: CAP_MESSAGE,
        },
        criteria: [
          ...Array.from({ length: 11 }, (_, n) => ({
            id: `q${n}`,
            text: `Queued criterion ${n}`,
            status: "queued" as const,
            round: 0,
          })),
          ...Array.from({ length: 18 }, (_, n) => ({
            id: `p${n}`,
            text: `Proposed criterion ${n}`,
            status: "proposed" as const,
            round: 0,
          })),
        ],
        ...overrides,
      });

    const renderLive = (overrides: Partial<FeatureLoop> = {}) =>
      wrap(
        <FeatureCard
          feature={liveShape(overrides)}
          workspaces={[]}
          defaultWorkspace=""
          busy={false}
          starting={false}
          {...handlers()}
        />,
      );

    const warnings = (container: HTMLElement) => [
      ...container.querySelectorAll("p.chat-wb-warn"),
    ];

    it("renders exactly one cap paragraph when both strings are the cap message", () => {
      const { container } = renderLive();

      // A count, not an absence: a later change that makes `reason` a *variant*
      // of the cap sentence would pass `queryAllByText(message)).toHaveLength(1)`
      // while stacking two paragraphs again.
      expect(warnings(container)).toHaveLength(1);
      expect(warnings(container)[0]).toHaveAttribute("role", "status");
      expect(warnings(container)[0]).toHaveTextContent(CAP_MESSAGE);
      expect(screen.getAllByRole("status")).toHaveLength(1);
    });

    // The dedupe must key on the strings, not on `at_cap`. A blocked ticket or
    // a round that stopped carries its own reason, and suppressing it whenever
    // the cap is reached hides why the loop is really stalled.
    it("renders a non-capacity reason beside the cap message, at the cap", () => {
      const reason =
        "Gate failed: lint. Clear it on the ticket, then the loop resumes.";

      const { container } = renderLive({ reason });

      expect(warnings(container).map((p) => p.textContent)).toEqual([
        reason,
        CAP_MESSAGE,
      ]);
      // Order matters as much as the count: the operator meets the reason and
      // the cap before the 29 rows and every control they disable.
      expect(screen.getAllByRole("status")).toHaveLength(2);
    });

    // A dedupe written as "drop the cap message whenever there is a reason"
    // passes the count above and loses the only line naming the counts.
    it("renders the cap message when the loop carries no reason at all", () => {
      const { container } = renderLive({ reason: "" });

      expect(warnings(container)).toHaveLength(1);
      expect(warnings(container)[0]).toHaveTextContent(CAP_MESSAGE);
    });

    // And the inverse mutant: "drop the reason whenever there is a cap message"
    // is invisible at the cap, where they are equal. Below it the message is
    // null, so a stalled loop must keep saying why.
    it("keeps a stalled reason below the cap, where there is no cap message", () => {
      const { container } = wrap(
        <FeatureCard
          feature={loop({ status: "blocked", reason: "Gate failed: lint" })}
          workspaces={[]}
          defaultWorkspace=""
          busy={false}
          starting={false}
          {...handlers()}
        />,
      );

      expect(warnings(container).map((p) => p.textContent)).toEqual([
        "Gate failed: lint",
      ]);
    });
  });

  describe("while the thread snapshot is on the wire", () => {
    // `feature: snapshot?.feature ?? null` collapses "not loaded" into "no
    // loop", so a thread that already has a loop flashed the start-a-loop empty
    // state on every cold open.
    it("draws the loop's shape instead of the no-loop empty state", () => {
      wrap(
        <FeatureCard
          feature={null}
          loading
          workspaces={["loregarden"]}
          defaultWorkspace="loregarden"
          busy={false}
          starting={false}
          {...handlers()}
        />,
      );

      const skeleton = screen.getByTestId("pane-skeleton");
      expect(skeleton).toHaveAttribute("data-variant", "list");
      expect(
        screen.queryByRole("button", { name: "Start a feature loop…" }),
      ).toBeNull();
      expect(screen.queryByText(/Turn this chat into a feature/)).toBeNull();
    });

    // A refetch over a thread that already has a loop must not blank the card:
    // the skeleton stands in for the first load only.
    it("keeps a loop already in hand on screen", () => {
      wrap(
        <FeatureCard
          feature={loop()}
          loading
          workspaces={[]}
          defaultWorkspace=""
          busy={false}
          starting={false}
          {...handlers()}
        />,
      );

      expect(screen.queryByTestId("pane-skeleton")).toBeNull();
      expect(
        screen.getByRole("button", { name: "Accept all" }),
      ).toBeInTheDocument();
    });

    // Not loading and no loop is still the empty state it always was.
    it("shows the start-a-loop empty state once the answer is in and there is no loop", () => {
      wrap(
        <FeatureCard
          feature={null}
          loading={false}
          workspaces={["loregarden"]}
          defaultWorkspace="loregarden"
          busy={false}
          starting={false}
          {...handlers()}
        />,
      );

      expect(screen.queryByTestId("pane-skeleton")).toBeNull();
      expect(
        screen.getByRole("button", { name: "Start a feature loop…" }),
      ).toBeInTheDocument();
    });
  });
});

describe("ChangesCard", () => {
  it("shows the chat's branch and the loop's runs, the live ones counted", () => {
    wrap(
      <ChangesCard
        branch={{
          branch: "chat/dark-mode",
          parent_branch: "main",
          worktree_path: "/w",
          has_conflicts: false,
        }}
        runs={[
          {
            id: "r1",
            run_code: "run_1",
            agent_id: "implementer",
            stage_key: "implement",
            status: "running",
            started_at: null,
            finished_at: null,
          },
          {
            id: "r2",
            run_code: "run_0",
            agent_id: "planner",
            stage_key: "plan",
            status: "succeeded",
            started_at: null,
            finished_at: null,
          },
        ]}
        hasLoop
      />,
    );

    expect(screen.getByText("chat/dark-mode")).toBeInTheDocument();
    expect(screen.getByText("1 running")).toBeInTheDocument();
    expect(
      within(screen.getByRole("list", { name: "Feature runs" })).getAllByRole(
        "listitem",
      ),
    ).toHaveLength(2);
  });

  it("says what will appear before anything has run", () => {
    wrap(<ChangesCard branch={null} runs={[]} hasLoop />);

    expect(screen.getByText(/No branch yet/)).toBeInTheDocument();
    expect(
      screen.getByText(/A round starts when you accept criteria/),
    ).toBeInTheDocument();
  });
});
