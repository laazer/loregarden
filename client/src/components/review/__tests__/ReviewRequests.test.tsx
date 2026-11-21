import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ReviewRequest } from "../../../api/reviewApi";
import { renderWithRouter } from "../../../test/renderWithRouter";
import { ReviewRequests } from "../ReviewRequests";

jest.mock("../../../api/localInstancesApi", () => ({
  localInstancesApi: { list: jest.fn().mockResolvedValue({ instances: [], unreadable: [] }) },
}));

function request(body: string): ReviewRequest {
  return { name: "Create enemy", method: "POST", path: "/api/enemies", body, expect_status: 201 };
}

describe("ReviewRequests", () => {
  it("lets a keyboard reach a long request body to scroll it", async () => {
    const user = userEvent.setup();
    renderWithRouter(<ReviewRequests requests={[request('{"kind": "slime"}')]} workspaceSlug="blobert" />);

    const body = screen.getByRole("group", { name: "Request body for Create enemy" });
    screen.getByLabelText("Send to").focus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Send" })).toHaveFocus();

    await user.tab();
    expect(body).toHaveFocus();
  });

  it("shows no body region for a request without a body", () => {
    renderWithRouter(<ReviewRequests requests={[request("")]} workspaceSlug="blobert" />);

    expect(screen.queryByRole("group", { name: "Request body for Create enemy" })).toBeNull();
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
  });
});
