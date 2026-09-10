/**
 * The toggle between the machine's two pools.
 *
 * `role="tablist"` with its own label matters more than it looks: the rail has
 * a tablist too, and two unlabelled ones are indistinguishable to anyone moving
 * by landmark.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { QueueKindToggle } from "../QueueKindToggle";

describe("QueueKindToggle", () => {
  it("presents the two pools as peers and marks the current one", () => {
    render(<QueueKindToggle value="agents" onChange={() => {}} />);

    const list = screen.getByRole("tablist", { name: "Queue type" });
    expect(list).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Agent lanes" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "Docker" })).toHaveAttribute("aria-selected", "false");
  });

  it("reports the choice rather than holding it", async () => {
    const onChange = jest.fn();
    render(<QueueKindToggle value="agents" onChange={onChange} />);

    await userEvent.click(screen.getByRole("tab", { name: "Docker" }));
    expect(onChange).toHaveBeenCalledWith("docker");
  });
});
