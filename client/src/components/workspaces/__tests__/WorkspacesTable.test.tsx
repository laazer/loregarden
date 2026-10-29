import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { localInstancesApi } from "../../../api/localInstancesApi";
import type { InstallState, WorkspaceIntegration } from "../../../api/localInstancesTypes";
import { pushToast } from "../../../state/toastStore";
import { WorkspacesTable } from "../WorkspacesTable";

jest.mock("../../../api/localInstancesApi", () => ({
  localInstancesApi: { integration: jest.fn(), installPullRequest: jest.fn() },
}));
jest.mock("../../../api/client", () => ({ api: { createWorkspaceRepository: jest.fn() } }));
jest.mock("../../../state/toastStore", () => ({
  ...jest.requireActual("../../../state/toastStore"),
  pushToast: jest.fn(),
}));
// The setup dialog's panels have their own tests.
jest.mock("../WorkspaceSetupDialog", () => ({
  WorkspaceSetupDialog: ({ workspace }: { workspace: { name: string } | null }) =>
    workspace ? <div role="dialog" aria-label={`Set up ${workspace.name}`} /> : null,
}));

const integrationApi = localInstancesApi.integration as jest.Mock;
const installPullRequest = localInstancesApi.installPullRequest as jest.Mock;

const STATES: Record<string, [InstallState, InstallState]> = {
  blobert: ["current", "outdated"],
  loregarden: ["built_in", "built_in"],
};

function integrationFor(slug: string): WorkspaceIntegration {
  const [hooks, docs] = STATES[slug];
  return {
    slug,
    repo_root: `/w/${slug}`,
    installers: [
      { installer: "hooks", state: hooks, detail: "" },
      { installer: "docs", state: docs, detail: "" },
    ],
  };
}

const workspace = (slug: string, name: string) => ({ slug, name, repo_root: `/w/${slug}`, entries: [] }) as never;
const summary = (slug: string, tickets: number, blocked: number) =>
  [slug, { slug, repo_state: "repository", archived_at: null, ticket_count: tickets, blocked_count: blocked }] as const;

function renderTable() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WorkspacesTable
          workspaces={[workspace("loregarden", "loregarden"), workspace("blobert", "Blobert")]}
          summaries={new Map([summary("loregarden", 1623, 8), summary("blobert", 164, 17)]) as never}
          onArchive={jest.fn()}
          archivingSlug={null}
          caption="Active workspaces"
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const names = () =>
  within(screen.getByRole("table"))
    .getAllByRole("rowheader")
    .map((cell) => cell.querySelector("a")?.textContent);

beforeEach(() => {
  integrationApi.mockReset().mockImplementation(async (slug: string) => integrationFor(slug));
  installPullRequest.mockReset();
  (pushToast as jest.Mock).mockReset();
});

describe("WorkspacesTable", () => {
  it("puts the workspaces that need something first, each name a link to its dashboard", async () => {
    renderTable();
    await screen.findByText("AGENTS.md outdated");
    expect(names()).toEqual(["Blobert", "loregarden"]);
    expect(screen.getByRole("link", { name: "Blobert" })).toHaveAttribute("href", "/workspaces/blobert");
    expect(screen.getByRole("columnheader", { name: /Needs/ })).toHaveAttribute("aria-sort", "descending");
  });

  it("opens a pull request only after confirming, then shows its link in place of the button", async () => {
    let finish: (value: unknown) => void = () => {};
    installPullRequest.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    renderTable();
    await userEvent.click(await screen.findByRole("button", { name: "Update AGENTS.md in Blobert…" }));
    expect(installPullRequest).not.toHaveBeenCalled();

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Your checkout at /w/blobert is not touched");
    await userEvent.click(within(dialog).getByRole("button", { name: "Open pull request" }));
    expect(installPullRequest).toHaveBeenCalledWith("blobert", "docs");
    // In flight: the dialog holds, and so does every other fix button.
    expect(within(dialog).getByRole("button", { name: "Opening pull request…" })).toBeDisabled();
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    finish({ outcome: "opened", detail: "https://github.com/laazer/blobert/pull/42" });
    const link = await screen.findByRole("link", { name: "laazer/blobert#42 opened ↗" });
    expect(link).toHaveAttribute("href", "https://github.com/laazer/blobert/pull/42");
    expect(screen.queryByRole("button", { name: /Update AGENTS.md/ })).not.toBeInTheDocument();
    expect(pushToast).toHaveBeenCalledWith(expect.objectContaining({ tone: "success", title: "Opened laazer/blobert#42" }));
  });

  it("keeps the dialog open with the reason when the pull request fails", async () => {
    installPullRequest.mockRejectedValue(new Error("gh has no token for 'laazer'"));
    renderTable();
    await userEvent.click(await screen.findByRole("button", { name: "Update AGENTS.md in Blobert…" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Open pull request" }));
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent("no token for 'laazer'");
  });

  it("says so when the branch already carries the block", async () => {
    installPullRequest.mockResolvedValue({ outcome: "no_change", detail: "ok: already current" });
    renderTable();
    await userEvent.click(await screen.findByRole("button", { name: "Update AGENTS.md in Blobert…" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Open pull request" }));
    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith(expect.objectContaining({ title: "Nothing to change in Blobert" })),
    );
  });

  it("keeps a failed check apart from a workspace that needs nothing", async () => {
    integrationApi.mockImplementation(async (slug: string) => {
      if (slug === "blobert") throw new Error("installer timed out");
      return integrationFor(slug);
    });
    renderTable();
    expect(await screen.findByText(/Check failed: installer timed out/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(integrationApi.mock.calls.filter(([slug]) => slug === "blobert")).toHaveLength(2));
  });

  it("opens setup from the row's menu", async () => {
    renderTable();
    await userEvent.click(await screen.findByRole("button", { name: "Blobert actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Set up…" }));
    expect(screen.getByRole("dialog", { name: "Set up Blobert" })).toBeInTheDocument();
  });
});
