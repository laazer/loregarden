import { fireEvent, render, screen } from "@testing-library/react";

import type { Approval } from "../../api/client";
import { IgnoreApprovalModal } from "../IgnoreApprovalModal";

function approval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: "appr_1",
    title: "Merge conflict: integration/ms cannot take main",
    level: "high",
    workspace_slug: "loregarden",
    stage_key: "review",
    stage_name: "Review",
    impact: "",
    ticket_id: "ticket_1",
    ticket_external_id: "lg-ms-1",
    kind: "human_action",
    run_id: "",
    tool_name: "",
    tool_input_json: "{}",
    cli_adapter: "",
    ticket_state: "in_progress",
    ignore_effect: "none",
    ...overrides,
  };
}

function renderModal(card: Approval, onConfirm = jest.fn(), hasDuplicate = false) {
  render(
    <IgnoreApprovalModal
      open
      approval={card}
      hasDuplicate={hasDuplicate}
      onClose={jest.fn()}
      onConfirm={onConfirm}
    />,
  );
  return onConfirm;
}

function presetNames(): string[] {
  const group = screen.getByRole("group", { name: "Common reasons" });
  return Array.from(group.querySelectorAll("button")).map((b) => b.textContent ?? "");
}

describe("IgnoreApprovalModal", () => {
  it("offers 'Ticket completed' only when the ticket is finished", () => {
    renderModal(approval());
    expect(presetNames()).not.toContain("Ticket completed");
  });

  it.each(["done", "wont_do"] as const)("offers 'Ticket completed' for a %s ticket", (state) => {
    renderModal(approval({ ticket_state: state }));
    expect(presetNames()[0]).toBe("Ticket completed");
  });

  it("sends the typed reason, trimmed", () => {
    const onConfirm = renderModal(approval());
    const confirm = screen.getByRole("button", { name: "Ignore" });
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByRole("textbox", { name: "Reason" }), {
      target: { value: "  filed by a test run  " },
    });
    fireEvent.click(confirm);

    expect(onConfirm).toHaveBeenCalledWith("filed by a test run");
  });

  it("writes a reason from the card's facts", () => {
    renderModal(approval(), jest.fn(), true);

    fireEvent.click(screen.getByRole("button", { name: "Write one for me" }));

    expect((screen.getByRole("textbox", { name: "Reason" }) as HTMLTextAreaElement).value).toMatch(
      /^Duplicate: .*lg-ms-1/,
    );
  });

  it.each([
    ["denies_agent", /agent is waiting/i],
    ["declines_stage", /Review is parked/],
  ] as const)("warns before ignoring a card that %s", (effect, warning) => {
    renderModal(approval({ ignore_effect: effect }));
    expect(screen.getByRole("alert")).toHaveTextContent(warning);
  });

  it("warns about nothing when nothing waits", () => {
    renderModal(approval());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
