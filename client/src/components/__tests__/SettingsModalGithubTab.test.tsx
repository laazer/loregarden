/**
 * Workspace-wide GitHub sync lives in Settings now, as its own tab: the topbar
 * button that opened it is gone, and one ticket syncs from its own header.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SettingsModal } from "../SettingsModal";

jest.mock("../GithubSyncPanel", () => ({
  GithubSyncPanel: () => <div data-testid="github-sync-panel" />,
}));

test("the GitHub tab shows the sync panel under its own heading", async () => {
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SettingsModal
        open
        workspaceSlug="loregarden"
        workspaces={[]}
        runtimeOptions={undefined}
        isSaving={false}
        onClose={jest.fn()}
        onWorkspaceChange={jest.fn()}
        onSave={jest.fn()}
      />
    </QueryClientProvider>,
  );

  const tab = screen.getByRole("tab", { name: "GitHub" });
  await user.click(tab);

  expect(tab).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("heading", { name: "Sync GitHub issues" })).toBeInTheDocument();
  expect(screen.getByTestId("github-sync-panel")).toBeInTheDocument();
});
