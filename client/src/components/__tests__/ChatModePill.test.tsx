import { fireEvent, render, screen } from "@testing-library/react";

import { ChatModePill } from "../ChatModePill";
import type { ChatMode } from "../../api/client";

const ACT: ChatMode = { mode: "act", cause: null, reason: "", advice: "", remediable: false };

const ADAPTER: ChatMode = {
  mode: "advisory",
  cause: "adapter_cannot_execute",
  reason: "The selected opencode adapter cannot execute turns.",
  advice: "Switch this workspace's agent runtime to one that can run tools.",
  remediable: true,
};

const BRANCH: ChatMode = {
  mode: "advisory",
  cause: "branch_not_checked_out",
  reason: "This branch is not checked out in a worktree.",
  advice: "Check the branch out into a worktree.",
  remediable: true,
};

const ASIDE: ChatMode = {
  mode: "advisory",
  cause: "aside_observer",
  reason: "An aside is answered by reading the running agent's log.",
  advice: "Nothing to fix.",
  remediable: false,
};

const INTERNAL: ChatMode = {
  mode: "advisory",
  cause: "no_run_for_approvals",
  reason: "This turn has no agent run to attach approvals to.",
  advice: "Nothing to change on your side.",
  remediable: false,
};

describe("ChatModePill", () => {
  it("says so when the rail can act, rather than staying silent", () => {
    render(<ChatModePill mode={ACT} />);
    expect(screen.getByText("can act")).toBeInTheDocument();
  });

  it("shows the server's reason and remedy on an advisory rail", () => {
    render(<ChatModePill mode={ADAPTER} />);
    const pill = screen.getByText("advisory").closest(".app-action-bar-pill");
    expect(pill).toHaveAttribute("title", expect.stringContaining("cannot execute turns"));
    expect(pill).toHaveAttribute("title", expect.stringContaining("Switch this workspace"));
  });

  it("offers the runtime fix for an adapter that cannot execute", () => {
    const onFix = jest.fn();
    render(<ChatModePill mode={ADAPTER} onFix={onFix} />);
    fireEvent.click(screen.getByRole("button", { name: "Change runtime" }));
    expect(onFix).toHaveBeenCalledWith("runtime");
  });

  it("offers the checkout fix for a branch with no worktree", () => {
    const onFix = jest.fn();
    render(<ChatModePill mode={BRANCH} onFix={onFix} />);
    fireEvent.click(screen.getByRole("button", { name: "Check out branch" }));
    expect(onFix).toHaveBeenCalledWith("checkout");
  });

  it("offers no fix for a cause the operator cannot clear", () => {
    render(<ChatModePill mode={INTERNAL} onFix={jest.fn()} />);
    expect(screen.getByText("advisory")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders nothing during an aside, which has its own label", () => {
    const { container } = render(<ChatModePill mode={ASIDE} asideMode />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing before the snapshot has resolved a mode", () => {
    const { container } = render(<ChatModePill mode={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows no fix button when the host passes no handler", () => {
    render(<ChatModePill mode={ADAPTER} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
