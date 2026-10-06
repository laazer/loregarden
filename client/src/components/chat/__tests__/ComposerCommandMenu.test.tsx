import { render } from "@testing-library/react";
import { createRef } from "react";

import type { ComposerMenuItem } from "../../../hooks/useComposerCommands";
import { BUILTIN_COMMANDS } from "../../../lib/composerCommands";
import { stubScrolling } from "../../../test/scrollStubs";
import { ComposerCommandMenu } from "../ComposerCommandMenu";

const ITEMS: ComposerMenuItem[] = BUILTIN_COMMANDS.map((command) => ({
  kind: "command",
  id: command.name,
  command,
}));

function menu(activeIndex: number) {
  return (
    <ComposerCommandMenu
      items={ITEMS}
      activeIndex={activeIndex}
      triggerKind="slash"
      anchorRef={createRef<HTMLInputElement>()}
      onHover={jest.fn()}
      onPick={jest.fn()}
    />
  );
}

test("arrowing past the fold scrolls the menu's own list to the highlight, and nothing around it", () => {
  // The list shows 0..280; the highlighted row sits at 300..330, just below it.
  const scrolling = stubScrolling(".lg-composer-menu { overflow-y: auto; }", (element) => {
    if (element.matches(".lg-composer-menu")) return { top: 0, bottom: 280 };
    if (element.matches(".lg-composer-menu-item.is-active")) return { top: 300, bottom: 330 };
    return null;
  });
  try {
    const { rerender, container } = render(menu(0));
    scrolling.scrollTo.mockClear();

    rerender(menu(ITEMS.length - 1));

    expect(scrolling.scrollTo.mock.instances).toEqual([container.querySelector(".lg-composer-menu")]);
    expect(scrolling.scrollTo).toHaveBeenCalledWith({ top: 50, behavior: "auto" });
    expect(scrolling.scrollIntoView).not.toHaveBeenCalled();
  } finally {
    scrolling.restore();
  }
});
