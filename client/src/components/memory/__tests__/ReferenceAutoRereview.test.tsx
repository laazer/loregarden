/**
 * Auto re-review from the reference library: the settings dialog (reached from
 * the toolbar or the sidebar's quick action), and taking one source out from
 * its page. The server decides what is due and queues it; these claims are
 * what the operator sets, sees and can undo.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";

import { api } from "../../../api/client";
import type { AutoRereviewSettings, LibraryDetail, LibraryList, LibraryRow } from "../../../api/referenceLibraryApi";
import { memoryAutoRereviewPath } from "../../../lib/appNavigation";
import { MemoryPage } from "../../../pages/MemoryPage";
import { useToastStore } from "../../../state/toastStore";
import deepSource from "../../../test/fixtures/prod-shape/reference-library-deep-source.json";
import libraryFixture from "../../../test/fixtures/prod-shape/reference-library-loregarden.json";
import { TopbarPageSlot, TopbarPageSlotProvider } from "../../TopbarPageSlot";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;
const LIBRARY = libraryFixture as unknown as LibraryList;
const DEEP = deepSource as unknown as LibraryDetail;

const OFF: AutoRereviewSettings = {
  enabled: false,
  min_new_commits: 25,
  max_commits_per_review: 50,
  max_commits_ceiling: 100,
  due: 44,
  in_flight: null,
  excluded: [],
  updated_at: null,
};

function Search() {
  return <span data-testid="search">{useLocation().search}</span>;
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <TopbarPageSlotProvider>
          <TopbarPageSlot />
          <MemoryPage />
          <Search />
        </TopbarPageSlotProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
  mockApi.workspaces.mockResolvedValue([
    { id: "w1", slug: "loregarden", name: "loregarden", archived_at: null },
  ] as Awaited<ReturnType<typeof api.workspaces>>);
  mockApi.referenceLibrary.mockResolvedValue(LIBRARY);
  mockApi.referenceSource.mockResolvedValue(DEEP);
  mockApi.autoRereviewSettings.mockResolvedValue(OFF);
});

it("opens from the quick action's link, turns on with both numbers, and says what is due", async () => {
  mockApi.saveAutoRereviewSettings.mockResolvedValue({ ...OFF, enabled: true, min_new_commits: 100, max_commits_per_review: 20, due: 12 });
  renderAt(memoryAutoRereviewPath());
  const dialog = await screen.findByRole("form", { name: "Auto re-review" });
  // One-shot, like the add form: the URL no longer asks for it once it is open.
  await waitFor(() => expect(screen.getByTestId("search")).toHaveTextContent(/^$/));
  expect(within(dialog).getByText("44 repositories are due at the saved threshold.")).toBeInTheDocument();

  fireEvent.click(within(dialog).getByRole("checkbox", { name: /Review a reference again/ }));
  fireEvent.change(within(dialog).getByRole("spinbutton", { name: /after this many new commits/ }), { target: { value: "100" } });
  fireEvent.change(within(dialog).getByRole("spinbutton", { name: /at most this many of the newest/ }), { target: { value: "20" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Save auto re-review" }));

  await waitFor(() =>
    expect(mockApi.saveAutoRereviewSettings).toHaveBeenCalledWith({ enabled: true, min_new_commits: 100, max_commits_per_review: 20 }),
  );
  await waitFor(() => expect(screen.queryByRole("form", { name: "Auto re-review" })).not.toBeInTheDocument());
  expect(useToastStore.getState().toasts[0]).toMatchObject({ tone: "success", title: "Auto re-review is on" });
  expect(screen.getByRole("button", { name: "Auto re-review: on" })).toHaveAttribute("aria-expanded", "false");
});

it("refuses a cap above what GitHub lists in one read, before sending", async () => {
  renderAt("/memory/library");
  fireEvent.click(await screen.findByRole("button", { name: "Auto re-review: off" }));
  const dialog = await screen.findByRole("form", { name: "Auto re-review" });
  const cap = within(dialog).getByRole("spinbutton", { name: /at most this many of the newest/ });
  fireEvent.change(cap, { target: { value: "250" } });
  expect(cap).toHaveAttribute("aria-invalid", "true");
  expect(within(dialog).getByText(/1 to 100 commits read/)).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Save auto re-review" })).toBeDisabled();
});

it("keeps the dialog and its edits open and names a failed save", async () => {
  mockApi.saveAutoRereviewSettings.mockRejectedValue(new Error("database is locked"));
  renderAt(memoryAutoRereviewPath());
  const dialog = await screen.findByRole("form", { name: "Auto re-review" });
  fireEvent.click(within(dialog).getByRole("checkbox", { name: /Review a reference again/ }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Save auto re-review" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("Not saved: database is locked");
  expect(within(dialog).getByRole("checkbox", { name: /Review a reference again/ })).toBeChecked();
});

it("lists the sources taken out, links each, and puts one back", async () => {
  mockApi.autoRereviewSettings.mockResolvedValue({
    ...OFF,
    excluded: [{ id: "s9", name: "noisy-repo" }],
    in_flight: { source_id: "s3", name: "busy-repo", ordinal: 4, status: "queued" },
  });
  mockApi.setSourceAutoRereview.mockResolvedValue({ ...LIBRARY.sources[0], id: "s9", auto_rereview_excluded: false });
  renderAt(memoryAutoRereviewPath());
  const dialog = await screen.findByRole("form", { name: "Auto re-review" });
  await waitFor(() => expect(screen.getByTestId("search")).toHaveTextContent(/^$/));
  expect(within(dialog).getByRole("link", { name: "busy-repo pass 4" })).toHaveAttribute("href", "/memory/library/s3");
  const taken = within(dialog).getByRole("region", { name: "Taken out of auto re-review" });
  expect(within(taken).getByRole("link", { name: "noisy-repo" })).toHaveAttribute("href", "/memory/library/s9");
  fireEvent.click(within(taken).getByRole("button", { name: "Put noisy-repo back in auto re-review" }));
  await waitFor(() => expect(mockApi.setSourceAutoRereview).toHaveBeenCalledWith("s9", false));
});

it("says the settings did not load and retries them", async () => {
  mockApi.autoRereviewSettings.mockRejectedValue(new Error("server down"));
  renderAt(memoryAutoRereviewPath());
  const dialog = await screen.findByRole("form", { name: "Auto re-review" });
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("The settings did not load: server down");
  expect(screen.getByRole("button", { name: "Auto re-review: unknown" })).toBeInTheDocument();
  mockApi.autoRereviewSettings.mockResolvedValue(OFF);
  fireEvent.click(within(dialog).getByRole("button", { name: "Retry" }));
  expect(await within(dialog).findByRole("checkbox", { name: /Review a reference again/ })).not.toBeChecked();
});

it("takes a repository out of auto re-review from its page", async () => {
  const source: LibraryRow = { ...DEEP.source, id: "s1", name: "widget", kind: "repo", auto_rereview_excluded: false };
  mockApi.referenceSource.mockResolvedValue({ ...DEEP, source });
  mockApi.setSourceAutoRereview.mockResolvedValue({ ...source, auto_rereview_excluded: true });
  renderAt("/memory/library/s1");
  expect(await screen.findByText("Included")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Take out of auto re-review" }));
  await waitFor(() => expect(mockApi.setSourceAutoRereview).toHaveBeenCalledWith("s1", true));
  await waitFor(() =>
    expect(useToastStore.getState().toasts[0]).toMatchObject({ tone: "success", title: "widget is out of auto re-review" }),
  );
});
