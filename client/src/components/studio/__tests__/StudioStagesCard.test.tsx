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

import type { RuntimeOptions, StudioExitActionRequirementCatalog } from "../../../api/client";
import { StudioStagesCard } from "../StudioStagesCard";
import { emptyStage, type StudioWorkflowDraft } from "../studioWorkflowHelpers";

const EXIT_ACTION_REQUIREMENTS: StudioExitActionRequirementCatalog = {
  requirement_kinds: ["runtime_capability", "credential", "authority", "operator_judgment"],
  capability_ids: ["http_test_client"],
  credential_keys: ["claude_profile"],
  authority_scopes: ["release:publish"],
};

const AGENTS = [
  { slug: "planner", name: "Planner", built_in: true, adapter: "cursor" },
  { slug: "backend_implementer", name: "Backend Implementer", built_in: true, adapter: "cursor" },
] as never[];

const CLAUDE_RUNTIME: RuntimeOptions = {
  cli_adapters: [],
  claude_models: [
    { id: "", label: "Default (Claude Code profile)" },
    { id: "claude-opus-5", label: "Claude Opus 5 (pinned)" },
    { id: "claude-sonnet-4-5", label: "Claude Sonnet 4.5" },
  ],
  cursor_models: [
    { id: "", label: "Default (Cursor profile)" },
    { id: "gpt-5", label: "GPT-5" },
  ],
  codex_models: [],
  lmstudio_models: [],
  opencode_models: [],
};

function draftWith(...stages: StudioWorkflowDraft["stages"]): StudioWorkflowDraft {
  return { slug: "wf", name: "WF", description: "", stages, transitions: [] };
}

/**
 * The card with the draft state the page owns, so an edit is observed the way
 * the page observes it: through the setter, and back into the rendered form.
 */
function renderCard(
  initial: StudioWorkflowDraft,
  readOnly = false,
  runtimeOptions: RuntimeOptions | undefined = undefined,
) {
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
        skills={["plan", "implement"]}
        exitActionRequirements={EXIT_ACTION_REQUIREMENTS}
        runtimeOptions={runtimeOptions}
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

describe("stage model override (R10)", () => {
  it("is a Claude-family select even when runtimeOptions has not arrived", () => {
    renderCard(draftWith(emptyStage(1)));
    const model = screen.getByLabelText("Model override");
    expect(model.tagName).toBe("SELECT");
    expect(within(model).getByRole("option", { name: "— Agent default —" })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Model id")).toBeNull();
  });

  it("uses the Claude family list, not cursor models and not the catalog empty-id row", () => {
    renderCard(draftWith(emptyStage(1)), false, CLAUDE_RUNTIME);
    const model = screen.getByLabelText("Model override");
    const options = within(model).getAllByRole("option");
    expect(options[0]).toHaveTextContent("— Agent default —");
    expect(within(model).getByRole("option", { name: "Claude Opus 5 (pinned)" })).toBeInTheDocument();
    expect(within(model).queryByRole("option", { name: "GPT-5" })).toBeNull();
    expect(within(model).queryByRole("option", { name: "Default (Claude Code profile)" })).toBeNull();
  });

  it("keeps a stored value that is not yet in the catalog", () => {
    const stage = { ...emptyStage(1), model: "claude-opus-legacy" };
    renderCard(draftWith(stage), false, CLAUDE_RUNTIME);
    const model = screen.getByLabelText("Model override");
    expect(model).toHaveValue("claude-opus-legacy");
    expect(within(model).getByRole("option", { name: "claude-opus-legacy" })).toBeInTheDocument();
  });

  it("hints that the pin applies only on a Claude harness", () => {
    renderCard(draftWith(emptyStage(1)), false, CLAUDE_RUNTIME);
    const model = screen.getByLabelText("Model override");
    const hint = screen.getByText("Applies only when this stage's run harness is Claude.");
    expect(model).toHaveAttribute("aria-describedby", hint.id);
  });
});
