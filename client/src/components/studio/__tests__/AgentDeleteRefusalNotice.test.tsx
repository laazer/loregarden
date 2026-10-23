/** Surface S2: what is still using this agent, and where to go change it.
 *
 * AC7 of lg-milestone-that-1239. Measured on the live database: the only two
 * agents whose Delete button renders at all are both named by the
 * `studio-blobert-ddd` template and the `blobert-ddd` draft, so under AC1's
 * reference scan the refusal is 100% of what this install's Delete button
 * produces. A nine-second toast holds no link, and the operator's next step is
 * to go edit the referencing workflow.
 */

import { fireEvent, render, screen } from "@testing-library/react";

import { AgentDeleteRefusalNotice } from "../AgentDeleteRefusalNotice";
import { isNavigableKind } from "../agentDeleteRefusal";

interface ReferenceClass {
  kind: string;
  ids: string[];
  count: number;
}

/** The 409 body the editor hands this component: a sentence for the toast and
 *  the structured halves for the links. */
interface Refusal {
  detail: string;
  references: { blocking: ReferenceClass[]; historical: ReferenceClass[] };
}

const REFUSAL: Refusal = {
  detail:
    "Cannot delete agent 'godot-ddd-migrator': 1 workflow template(s) " +
    "(studio-blobert-ddd) and 1 draft workflow(s) (blobert-ddd) still name it.",
  references: {
    blocking: [
      { kind: "workflow_template", ids: ["studio-blobert-ddd"], count: 1 },
      { kind: "studio_workflow", ids: ["blobert-ddd"], count: 1 },
    ],
    historical: [],
  },
};

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

describe("AgentDeleteRefusalNotice", () => {
  it("renders nothing when the last delete did not refuse", () => {
    const { container } = renderNotice(null);

    expect(container).toBeEmptyDOMElement();
  });

  it("is a status region, so it is announced without stealing focus", () => {
    renderNotice(REFUSAL);

    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("names the refusal in the panel, not only in the toast", () => {
    renderNotice(REFUSAL);

    expect(screen.getByRole("status")).toHaveTextContent("studio-blobert-ddd");
    expect(screen.getByRole("status")).toHaveTextContent("blobert-ddd");
  });

  it("makes every referencing workflow and template a control that navigates to it", () => {
    const { onOpenWorkflow } = renderNotice(REFUSAL);

    const template = screen.getByRole("button", { name: /studio-blobert-ddd/ });
    fireEvent.click(template);
    expect(onOpenWorkflow).toHaveBeenCalledWith("studio-blobert-ddd");

    fireEvent.click(screen.getByRole("button", { name: /^blobert-ddd$/ }));
    expect(onOpenWorkflow).toHaveBeenCalledWith("blobert-ddd");
  });

  it("renders non-navigable references as counts with their ids", () => {
    // A live ticket pinned through `next_agent` and the run ledger are not
    // places the operator can be sent; they are what the refusal has to report.
    renderNotice({
      detail: "Cannot delete agent 'planner': 65 live ticket(s) are pinned to it.",
      references: {
        blocking: [
          {
            kind: "ticket_next_agent",
            ids: ["lg-milestone-that-1239", "lg-milestone-that-975"],
            count: 65,
          },
        ],
        historical: [{ kind: "agent_run", ids: [], count: 491 }],
      },
    });

    const notice = screen.getByRole("status");
    expect(notice).toHaveTextContent("65");
    expect(notice).toHaveTextContent("lg-milestone-that-1239");
    expect(notice).toHaveTextContent("491");
    expect(screen.queryByRole("button", { name: /lg-milestone-that-1239/ })).toBeNull();
  });

  it("shows at most ten ids for a class while naming its true total", () => {
    // `planner` carries 8 templates, 3 drafts, 65 live tickets, 491 runs and
    // 379 tool calls on the live database.
    const ids = Array.from({ length: 10 }, (_, index) => `tpl-${index}`);
    renderNotice({
      detail: "Cannot delete agent 'planner': 40 workflow template(s) still name it.",
      references: {
        blocking: [{ kind: "workflow_template", ids, count: 40 }],
        historical: [],
      },
    });

    expect(screen.getAllByRole("button", { name: /^tpl-\d$/ })).toHaveLength(10);
    expect(screen.getByRole("status")).toHaveTextContent("40");
  });

  it("uses the app's button primitive for its controls", () => {
    // AC13: `ts-theme` rejects a raw <button> on a changed line.
    renderNotice(REFUSAL);

    for (const control of screen.getAllByRole("button")) {
      expect(control.className).toMatch(/btn-|ui-button/);
    }
  });

  it("drops the notice when a different refusal replaces it", () => {
    const { rerender } = renderNotice(REFUSAL);

    rerender(
      <AgentDeleteRefusalNotice
        refusal={null}
        onOpenWorkflow={jest.fn()}
        resolveWorkflowSlug={() => null}
      />,
    );

    expect(screen.queryByRole("status")).toBeNull();
  });
});
