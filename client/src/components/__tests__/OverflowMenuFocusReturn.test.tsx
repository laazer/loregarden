import { fireEvent, render, screen } from "@testing-library/react";

import { OverflowMenu, OverflowMenuItem } from "../OverflowMenu";

/**
 * AC20. The menu's one slow item — "Copy terminal command" — now takes as long
 * as a merge of up to 134 commits, and `OverflowMenu` closes on select. A
 * keyboard operator who selects it is dropped on `<body>` for those seconds,
 * with no way back to the control they just used, and the outcome is announced
 * only by `ToastHost`'s `aria-live="polite"` region.
 *
 * No new control: the trigger keeps its `aria-label` / `aria-expanded` /
 * `aria-haspopup`, and the items stay `role="menuitem"` buttons.
 */
function open(onSelect = jest.fn()) {
  render(
    <OverflowMenu label="Specification actions">
      <OverflowMenuItem onSelect={onSelect}>Copy terminal command</OverflowMenuItem>
    </OverflowMenu>,
  );
  const trigger = screen.getByRole("button", { name: "Specification actions" });
  fireEvent.click(trigger);
  return { trigger, onSelect };
}

describe("OverflowMenu focus", () => {
  it("returns focus to the trigger after an item is selected", () => {
    const { trigger, onSelect } = open();

    fireEvent.click(screen.getByRole("menuitem", { name: "Copy terminal command" }));

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("returns focus to the trigger when the panel is dismissed with Escape", () => {
    const { trigger } = open();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("still reports its open state on the trigger", () => {
    const { trigger } = open();

    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(trigger.getAttribute("aria-haspopup")).toBe("menu");

    fireEvent.keyDown(document, { key: "Escape" });

    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("does not steal focus before the menu has ever been opened", () => {
    // A focus return that fires on mount would pull focus to every stage row's
    // ⋯ button as a ticket's twelve-stage workflow renders.
    render(
      <OverflowMenu label="Planning actions">
        <OverflowMenuItem onSelect={jest.fn()}>Copy terminal command</OverflowMenuItem>
      </OverflowMenu>,
    );

    expect(document.activeElement).toBe(document.body);
  });
});
