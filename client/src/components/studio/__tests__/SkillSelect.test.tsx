import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SkillSelect } from "../SkillSelect";

it("renders the selected unlisted skill and does not call onChange while rendering", () => {
  const onChange = jest.fn();

  render(
    <SkillSelect
      className="studio-stage-select mono"
      disabled={false}
      skills={["autopilot", "plan"]}
      value="verify"
      onChange={onChange}
    />,
  );

  const select = screen.getByRole("combobox") as HTMLSelectElement;
  const unlistedOption = screen.getByRole("option", { name: "verify (unlisted)" }) as HTMLOptionElement;
  expect(select.value).toBe("verify");
  expect(unlistedOption.selected).toBe(true);
  expect(onChange).not.toHaveBeenCalled();
});

it("selects the empty option when the operator clears the skill", async () => {
  const user = userEvent.setup();
  const onChange = jest.fn();

  render(
    <SkillSelect
      className="studio-stage-select mono"
      disabled={false}
      skills={["autopilot", "plan"]}
      value="plan"
      onChange={onChange}
    />,
  );

  await user.selectOptions(screen.getByRole("combobox"), "");

  expect(onChange).toHaveBeenCalledWith("");
});
