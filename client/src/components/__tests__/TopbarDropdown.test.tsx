import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LazyMotion, MotionConfig, domAnimation } from "motion/react";

import { TopbarDropdown, TopbarDropdownItem } from "../TopbarDropdown";

function renderDropdown(onSelect: () => void) {
  return render(
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">
        <TopbarDropdown label="Sort">
          <TopbarDropdownItem onSelect={onSelect}>Newest</TopbarDropdownItem>
        </TopbarDropdown>
      </MotionConfig>
    </LazyMotion>,
  );
}

it("opens the menu from its trigger", async () => {
  const user = userEvent.setup();
  renderDropdown(jest.fn());

  const trigger = screen.getByRole("button", { name: /Sort/ });
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();

  await user.click(trigger);

  expect(screen.getByRole("menu")).toBeInTheDocument();
  expect(trigger).toHaveAttribute("aria-expanded", "true");
});

it("closes after a choice, and the menu leaves once its exit has played", async () => {
  const user = userEvent.setup();
  const onSelect = jest.fn();
  renderDropdown(onSelect);

  await user.click(screen.getByRole("button", { name: /Sort/ }));
  await user.click(screen.getByRole("menuitemradio", { name: "Newest" }));

  expect(onSelect).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: /Sort/ })).toHaveAttribute("aria-expanded", "false");
  await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
});
