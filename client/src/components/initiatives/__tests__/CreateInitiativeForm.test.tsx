/**
 * The new-initiative form's attached-milestone list, and its keyboard path.
 *
 * lg-milestone-that-2215: `.initiative-create-picked-list` is a bounded
 * `overflow: auto` <ul> (`max-height: 160px`) of every milestone ticked on the
 * page. Its rows are plain <li> text and the form's controls are elsewhere, so
 * once more than about seven were ticked the rest could only be reached with a
 * mouse. The region was also invisible to the census until this ticket, because
 * the rule's subject was an unclassed <ul>; the fix starts by giving it a class.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CreateInitiativeForm } from "../CreateInitiativeForm";
import type { InitiativeMilestone } from "../../../api/initiativeApi";

function milestone(id: string, title: string): InitiativeMilestone {
  return {
    id,
    title,
    workspace_slug: "loregarden",
    member: false,
    home_milestone: "",
  } as unknown as InitiativeMilestone;
}

const PICKED = [
  milestone("m1", "Reachable scrolling regions"),
  milestone("m2", "Merge queue durability"),
  milestone("m3", "Memory graph exports"),
];

const REGION = "Milestones this initiative will attach";

function renderForm(milestones: InitiativeMilestone[]) {
  const onCancel = jest.fn();
  render(
    <CreateInitiativeForm
      milestones={milestones}
      isSaving={false}
      onCreate={jest.fn().mockResolvedValue(undefined)}
      onCancel={onCancel}
    />,
  );
  return { onCancel };
}

describe("the attached-milestone list's keyboard path", () => {
  it("is a list, named, and a tab stop stepping on to Cancel — AC3", async () => {
    renderForm(PICKED);

    // `getByRole("list")`, not "group": a <ul> keeps its native `list` role,
    // so it still announces "list, 3 items".
    const list = screen.getByRole("list", { name: REGION });
    expect(list).toHaveAttribute("tabindex", "0");
    expect(list).toHaveClass("scroll-focus-ring");

    // The form's actions FOLLOW the list, so Tab forward is the step this
    // surface offers, and `Cancel` is the first of them.
    list.focus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("renders no labelled list when nothing is ticked — AC8", () => {
    renderForm([]);

    // Zero rows puts one line in its place, saying how to get one, rather
    // than a named box over nothing.
    expect(screen.queryByRole("list", { name: REGION })).toBeNull();
    expect(screen.getByText(/Tick milestones below to attach them/)).toBeInTheDocument();
  });

  it("lets Escape from inside the list still cancel the form — AC9", () => {
    const { onCancel } = renderForm(PICKED);

    const list = screen.getByRole("list", { name: REGION });
    list.focus();
    fireEvent.keyDown(list, { key: "Escape" });
    // The form handles Escape on itself; a region that consumed the key would
    // leave the operator unable to back out from inside the list.
    expect(onCancel).toHaveBeenCalled();
  });
});
