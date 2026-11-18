import { screen } from "@testing-library/react";

import { lookup, renderPanel } from "./ticketPullRequestPanelFixtures";

jest.mock("../../../api/client");

/**
 * A member finished after its tree's PR merged (lg-code-knowledge-1055 → #894).
 * The server no longer hands that PR over as the member's; the panel says why
 * the member has none, and where its work goes instead.
 */
describe("TicketPullRequestPanel — the tree's last PR finished without this work", () => {
  const earlier = { number: 894, url: "https://github.com/o/r/pull/894", state: "merged" as const };

  it("names the finished PR, links it, and says the work goes in the next one", async () => {
    renderPanel(
      lookup({
        lookup: "none",
        pull_request: null,
        branch: "integration/lg-ms-1",
        ships_with: "integration/lg-ms-1",
        earlier_tree_pull_request: earlier,
      }),
    );

    expect(await screen.findByText("Ships in its tree’s pull request")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "#894" })).toHaveAttribute("href", earlier.url);
    expect(screen.getByText(/merged before this ticket’s work reached the tree/)).toBeInTheDocument();
    expect(screen.getByText(/goes in the tree’s next PR/)).toBeInTheDocument();
  });

  it("says closed for a closed one", async () => {
    renderPanel(
      lookup({
        lookup: "none",
        pull_request: null,
        ships_with: "integration/lg-ms-1",
        earlier_tree_pull_request: { ...earlier, state: "closed" },
      }),
    );

    expect(await screen.findByText(/closed before this ticket’s work reached the tree/)).toBeInTheDocument();
  });

  it("says nothing of an earlier PR when there was none", async () => {
    renderPanel(lookup({ lookup: "none", pull_request: null, ships_with: "integration/lg-ms-1" }));

    expect(await screen.findByText("Ships in its tree’s pull request")).toBeInTheDocument();
    expect(screen.queryByText(/before this ticket’s work reached the tree/)).not.toBeInTheDocument();
  });
});
