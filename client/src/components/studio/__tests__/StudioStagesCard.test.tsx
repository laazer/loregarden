/**
 * The Studio's stage editor, which had no tests at all before it was extracted.
 *
 * That absence is why this file exists rather than a characterization test
 * against the page: there was nothing to characterize *against*. The split's
 * own evidence is that the JSX moved with nine differing lines out of five
 * hundred, all of them `query.data` becoming a prop. These tests are the
 * coverage the card should have had, added where it is now cheap to render one.
 *
 * They assert what an operator does to a stage and what comes back out through
 * `setWorkflowDraft` — not markup, which a refactor is allowed to change.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { StudioStagesCard } from "../StudioStagesCard";
import { emptyStage, type StudioWorkflowDraft } from "../studioWorkflowHelpers";

const AGENTS = [
  { slug: "planner", name: "Planner", built_in: true, adapter: "claude" },
  { slug: "backend_implementer", name: "Backend Implementer", built_in: true, adapter: "claude" },
] as never[];

function draftWith(...stages: StudioWorkflowDraft["stages"]): StudioWorkflowDraft {
  return { slug: "wf", name: "WF", description: "", stages, transitions: [] };
}

/**
 * The card with the draft state the page owns, so an edit is observed the way
 * the page observes it: through the setter, and back into the rendered form.
 */
function renderCard(initial: StudioWorkflowDraft, readOnly = false) {
  const seen: StudioWorkflowDraft[] = [];
  function Host() {
    const [draft, setDraft] = useState(initial);
    seen.push(draft);
    return (
      <StudioStagesCard
        workflowDraft={draft}
        setWorkflowDraft={setDraft}
        isWorkflowReadOnly={readOnly}
        agentOptions={AGENTS.map((a: { slug: string; name: string }) => ({
          id: a.slug,
          label: a.name,
        }))}
        agents={AGENTS}
        skills={["plan", "implement"]}
        runtimeOptions={undefined}
        skipConditions={["has_description", "routed_as_light_work"]}
        selectedWorkflow={null}
      />
    );
  }
  const rendered = render(<Host />);
  return { ...rendered, latest: () => seen[seen.length - 1] };
}

describe("the stage list", () => {
  it("shows a stage per entry, and counts them", () => {
    const { container } = renderCard(draftWith(emptyStage(1), emptyStage(2)));
    expect(screen.getByText("Stages")).toBeInTheDocument();
    // The count badge specifically: a bare `getByText("2")` matches the stage
    // *number* on the second row as well, and would pass with the badge gone.
    expect(container.querySelector(".studio-stage-count")).toHaveTextContent("2");
  });

  it("adds a stage at the end, numbered after the last", async () => {
    const user = userEvent.setup();
    const { latest } = renderCard(draftWith(emptyStage(1)));

    await user.click(screen.getByRole("button", { name: /add stage/i }));

    expect(latest().stages).toHaveLength(2);
    expect(latest().stages[1].order).toBe(2);
  });

  it("offers no way to add one when the workflow is read-only", () => {
    // A built-in workflow is shown, not edited. A control that appeared and
    // then refused would be worse than no control.
    renderCard(draftWith(emptyStage(1)), true);
    expect(screen.queryByRole("button", { name: /add stage/i })).toBeNull();
  });
});

describe("editing a stage", () => {
  it("writes a renamed stage back through the setter", async () => {
    const user = userEvent.setup();
    const { latest } = renderCard(draftWith(emptyStage(1)));

    const name = screen.getAllByDisplayValue("Stage 1")[0];
    await user.clear(name);
    await user.type(name, "Triage");

    expect(latest().stages[0].name).toBe("Triage");
  });

  it("edits only the stage that was touched", async () => {
    // The handlers map over every stage; an index mistake rewrites the wrong
    // one, and with identical defaults that is invisible in the markup.
    const user = userEvent.setup();
    const { latest } = renderCard(draftWith(emptyStage(1), emptyStage(2)));

    const second = screen.getAllByDisplayValue("Stage 2")[0];
    await user.clear(second);
    await user.type(second, "Verify");

    expect(latest().stages.map((stage) => stage.name)).toEqual(["Stage 1", "Verify"]);
  });

  it("changes a stage's type", async () => {
    const user = userEvent.setup();
    const { latest } = renderCard(draftWith(emptyStage(1)));

    const type = screen.getAllByRole("combobox").find((select) =>
      within(select).queryByRole("option", { name: /classify/i }),
    );
    expect(type).toBeDefined();
    await user.selectOptions(type as HTMLElement, "classify");

    expect(latest().stages[0].stage_type).toBe("classify");
  });
});
