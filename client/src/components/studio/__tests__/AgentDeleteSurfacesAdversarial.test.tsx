/** The two new agent-delete surfaces, pushed at their edges.
 *
 * AC7, AC10, AC11 and AC13 of lg-milestone-that-1239. The contract tests give
 * each component the payload the server is specified to send. This file gives
 * them the ones it will send anyway: a reference class nobody has taught the
 * component about, a class whose ids were truncated away, a refusal with
 * nothing navigable in it, and a version count of zero.
 *
 * The question behind every assertion is AC7's own: "can I remove this agent,
 * and if not, what is still using it that I must change first?" A notice that
 * renders an empty card, or silently drops a class it does not recognise,
 * answers it wrongly rather than not at all — which is worse, because the
 * operator reads it as "nothing else is in the way".
 */

import { fireEvent, render, screen } from "@testing-library/react";

import { AgentDeleteConfirm } from "../AgentDeleteConfirm";
import { AgentDeleteRefusalNotice } from "../AgentDeleteRefusalNotice";
import { isNavigableKind } from "../agentDeleteRefusal";

interface ReferenceClass {
  kind: string;
  ids: string[];
  count: number;
}

interface Refusal {
  detail: string;
  references: { blocking: ReferenceClass[]; historical: ReferenceClass[] };
}

/** The identity mapping, for the cases where the id is its own destination.
 *  Which ids have a destination at all is `resolveWorkflowDestination`'s job
 *  and is pinned in `agentDeleteRefusal.test.ts`; here it is a given. */
function renderNotice(
  refusal: Refusal | null,
  onOpenWorkflow = jest.fn(),
  resolveWorkflowSlug: (kind: string, id: string) => string | null = (kind, id) =>
    isNavigableKind(kind) ? id : null,
) {
  const result = render(
    <AgentDeleteRefusalNotice
      refusal={refusal}
      onOpenWorkflow={onOpenWorkflow}
      resolveWorkflowSlug={resolveWorkflowSlug}
    />,
  );
  return { ...result, onOpenWorkflow };
}

function refusalWith(blocking: ReferenceClass[], historical: ReferenceClass[] = []): Refusal {
  return {
    detail: "Cannot delete agent 'godot-ddd-migrator': something still names it.",
    references: { blocking, historical },
  };
}

describe("AgentDeleteRefusalNotice at its edges", () => {
  it("still says something when the refusal carries no reference classes", () => {
    // Either half can be empty on the wire — the server sends only non-empty
    // classes, and a demotion (A3) empties one. An empty card is a surface
    // that ends in nothing, so the sentence is the floor.
    renderNotice(refusalWith([]));

    const notice = screen.getByRole("status");
    expect(notice.textContent?.trim().length ?? 0).toBeGreaterThan(0);
  });

  it("reports a reference class it was never taught about", () => {
    // The blocking set is specified to be demotable in one line (AC16's two
    // halves), so a class will be added or moved after this component ships.
    // Rendering only the kinds in a switch statement drops it silently, and
    // the operator is told less than the server said.
    renderNotice(
      refusalWith([{ kind: "some_future_table", ids: ["thing-1"], count: 3 }]),
    );

    const notice = screen.getByRole("status");
    expect(notice).toHaveTextContent("thing-1");
    expect(notice).toHaveTextContent("3");
  });

  it("reports a class whose ids were all truncated away", () => {
    // `agent_run` arrives as ids: [] with count: 491 — the count is the whole
    // message, and a component that renders a class only when it has ids
    // shows an empty row or nothing at all.
    renderNotice(refusalWith([], [{ kind: "agent_run", ids: [], count: 491 }]));

    expect(screen.getByRole("status")).toHaveTextContent("491");
  });

  it.each([
    ["workflow_template", "blocking", true],
    ["studio_workflow", "blocking", true],
    ["ticket_next_agent", "blocking", false],
    ["ticket_scope_reroute_agent", "blocking", false],
    ["agent_run", "historical", false],
    ["mcp_tool_call", "historical", false],
    ["stage_fanout_group", "historical", false],
  ] as const)("renders %s in the %s half, navigable: %s", (kind, half, navigable) => {
    // AC7 draws the line at "a link per referencing workflow or template";
    // everything else is a count with its ids. A ticket id rendered as a
    // control that navigates to Studio → Workflows → <ticket id> lands the
    // operator on a workflow that does not exist. Navigability is derived from
    // `kind` in the component, so a kind landing on the wrong side of that
    // derivation is a wrong link rather than a missing one.
    const entry = { kind, ids: ["ref-1"], count: 1 };
    renderNotice(
      half === "blocking"
        ? refusalWith([entry])
        : refusalWith([{ kind: "workflow_template", ids: ["tpl-x"], count: 1 }], [entry]),
    );

    expect(screen.getByRole("status")).toHaveTextContent("ref-1");
    expect(screen.queryByRole("button", { name: /^ref-1$/ }) !== null).toBe(navigable);
  });

  it("keeps the two halves apart when both are populated", () => {
    // The operator acts on the blocking half and reads the historical one.
    // Collapsing them into one list makes a ledger row look like something to
    // go and fix, which is the refusal-with-no-exit shape (A7).
    const { onOpenWorkflow } = renderNotice(
      refusalWith(
        [{ kind: "workflow_template", ids: ["tpl-blocks"], count: 1 }],
        [{ kind: "agent_run", ids: ["RUN-1"], count: 24 }],
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: /^tpl-blocks$/ }));
    expect(onOpenWorkflow).toHaveBeenCalledWith("tpl-blocks");
    expect(screen.queryByRole("button", { name: /^RUN-1$/ })).toBeNull();
  });

  it("navigates with the id it displayed, every time it is clicked", () => {
    // Determinism, and the one thing the operator cannot check: a link whose
    // handler closes over the wrong index sends them to another workflow.
    const ids = ["tpl-a", "tpl-b", "tpl-c"];
    const { onOpenWorkflow } = renderNotice(
      refusalWith([{ kind: "workflow_template", ids, count: 3 }]),
    );

    for (const id of [...ids, ...ids]) {
      fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${id}$`) }));
    }

    expect(onOpenWorkflow.mock.calls.map(([slug]) => slug)).toEqual([...ids, ...ids]);
  });
});

interface ConfirmOverrides {
  open?: boolean;
  slug?: string;
  versionCount?: number;
  pending?: boolean;
  onConfirm?: () => void;
  onCancel?: () => void;
}

function renderConfirm(overrides: ConfirmOverrides = {}) {
  const props = {
    open: true,
    slug: "godot-ddd-migrator",
    versionCount: 11,
    pending: false,
    onConfirm: jest.fn(),
    onCancel: jest.fn(),
    ...overrides,
  };
  return { ...render(<AgentDeleteConfirm {...props} />), props };
}

describe("AgentDeleteConfirm at its edges", () => {
  it("dismisses on the backdrop while idle", () => {
    // The other half of `onDismiss`: ModalShell wires Escape *and* the
    // overlay's click to the same callback, so a confirm that tested only
    // Escape has tested only half of AC10's dismissal.
    const { props } = renderConfirm();

    const overlay = document.querySelector(".modal-overlay");
    expect(overlay).not.toBeNull();
    fireEvent.click(overlay!);

    expect(props.onCancel).toHaveBeenCalledTimes(1);
  });

  it("cannot be dismissed by the backdrop while the delete is in flight", () => {
    // AC10: `onDismiss` is `undefined` while pending. A dialog that closes
    // under a click while its request is away leaves the operator with no
    // report of how it ended — the refusal notice has nowhere to appear from.
    const { props } = renderConfirm({ pending: true });

    const overlay = document.querySelector(".modal-overlay");
    fireEvent.click(overlay!);

    expect(props.onCancel).not.toHaveBeenCalled();
  });

  it("does not take the destroy from Enter on the backdrop or a stray keypress", () => {
    const { props } = renderConfirm();

    fireEvent.keyDown(document, { key: "Enter" });

    expect(props.onConfirm).not.toHaveBeenCalled();
  });

  it.each([0, 1, 17])("names a version count of %i without reading as a gap", (versionCount) => {
    // Measured: agents carry 10.9 version rows and up to 17. Zero is reachable
    // the moment a restore path trims history, and "and its versions" with no
    // number is the sentence this criterion exists to prevent.
    renderConfirm({ versionCount });

    expect(screen.getByRole("dialog")).toHaveTextContent(String(versionCount));
  });

  it("names the agent it is about to destroy, not the one it was opened on last", () => {
    // The confirm is driven by props, so a reopen on another agent must redraw.
    // A copy of the slug kept in the component's own state shows the previous
    // one, and the operator confirms a destroy of an agent they did not pick.
    const { rerender } = renderConfirm({ slug: "first-agent" });
    expect(screen.getByRole("dialog")).toHaveTextContent("first-agent");

    rerender(
      <AgentDeleteConfirm
        open
        slug="second-agent"
        versionCount={4}
        pending={false}
        onConfirm={jest.fn()}
        onCancel={jest.fn()}
      />,
    );

    expect(screen.getByRole("dialog")).toHaveTextContent("second-agent");
    expect(screen.getByRole("dialog")).not.toHaveTextContent("first-agent");
  });

  it("stops announcing a dialog that is closed", () => {
    const { rerender } = renderConfirm();

    rerender(
      <AgentDeleteConfirm
        open={false}
        slug="godot-ddd-migrator"
        versionCount={11}
        pending={false}
        onConfirm={jest.fn()}
        onCancel={jest.fn()}
      />,
    );

    // ModalShell keeps the panel mounted for its exit, and marks it
    // `aria-hidden` and `inert` the moment it starts leaving.
    const dialog = document.querySelector('[role="dialog"]');
    if (dialog) expect(dialog).toHaveAttribute("aria-hidden", "true");
  });
});
