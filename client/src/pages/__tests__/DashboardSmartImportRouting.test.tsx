import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { Dashboard } from "../Dashboard";
import { RouterBridgeSync } from "../../components/RouterBridgeSync";
import * as apiClient from "../../api/client";
import { useUiStore } from "../../state/uiStore";

/**
 * Integration test suite for smart import routing in Dashboard.
 *
 * Ticket:   34-route-smart-import-selection-to-studio-with-prev
 *
 * These tests verify the end-to-end flow from ImportTicketsModal through Dashboard
 * handlers to Studio navigation:
 *
 * 1. Smart import mode creates a preview Ticket Studio session and navigates there
 *    (skipping the regular confirmation modal).
 * 2. Regular import mode continues to use the existing confirmation modal flow.
 * 3. Imported ticket data flows from file paths -> preview -> Studio session.
 * 4. Errors during smart import surface in the picker modal instead of navigating.
 */

jest.mock("../../api/client", () => jest.requireActual("../../test/apiClientMock"));

jest.mock("../../components/ImportTicketFileExplorer", () => {
  const FIXTURE_FILES = [
    { path: "features/auth.md", name: "auth.md", repo_path: "features/auth.md" },
  ];

  return {
    __esModule: true,
    ImportTicketFileExplorer: (props: {
      selectedFiles: Map<string, { path: string; name: string; repo_path: string }>;
      onToggleFile: (
        file: { path: string; name: string; repo_path: string },
        checked: boolean,
      ) => void;
      disabled?: boolean;
    }) => (
      <div data-testid="mock-file-explorer">
        {FIXTURE_FILES.map((file) => {
          const checked = props.selectedFiles.has(file.path);
          return (
            <button
              key={file.path}
              type="button"
              data-testid={`toggle-${file.path}`}
              aria-pressed={checked}
              disabled={props.disabled}
              onClick={() => props.onToggleFile(file, !checked)}
            >
              {file.repo_path}
            </button>
          );
        })}
      </div>
    ),
  };
});

const navigateToStudioTicketSession = jest.fn();
jest.mock("../../lib/useAppNavigation", () => ({
  ...jest.requireActual("../../lib/useAppNavigation"),
  navigateToStudioTicketSession: (...args: unknown[]) => navigateToStudioTicketSession(...args),
}));

const mkWorkspace = (over: Partial<apiClient.WorkspaceSummary> = {}): apiClient.WorkspaceSummary => ({
  id: "ws-1",
  slug: "loregarden",
  name: "Loregarden",
  repo_path: ".",
  repo_root: "/repo",
  repo_exists: true,
  ticket_count: 0,
  blocked_count: 0,
  workflow_template_slug: "",
  cli_adapter: "",
  claude_model: "",
  cursor_model: "",
  lmstudio_base_url: "",
  lmstudio_model: "",
  ...over,
});

const samplePreview = (over: Partial<apiClient.TicketImportPreviewResponse> = {}): apiClient.TicketImportPreviewResponse => ({
  tickets: [
    {
      title: "Imported Feature",
      work_item_type: "feature",
      description: "Feature imported via smart import.",
      acceptance_criteria: ["Criterion one"],
      external_id: "imported-feature",
    },
  ],
  errors: [],
  warnings: [],
  total: 1,
  by_type: { feature: 1 },
  formats: ["markdown"],
  show_preview: true,
  ...over,
});

describe("Dashboard - Smart Import Routing Integration Tests", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    useUiStore.setState({
      stateFilters: [],
      typeFilters: [],
      search: "",
      expandedTicketIds: [],
      workspace: "all",
      paneVisibility: { workspaces: true, tickets: true, workflow: true, artifacts: true },
    });
    useUiStore.persist?.clearStorage?.();
    jest.clearAllMocks();
    jest.mocked(apiClient.api.runs).mockResolvedValue([]);
    jest.mocked(apiClient.api.triage).mockResolvedValue({
      pending_approvals: [],
      recent_approvals: [],
      messages: [],
      runtime: { cli_adapter: "", claude_model: "", cursor_model: "", lmstudio_base_url: "", lmstudio_model: "" },
    });
    jest.mocked(apiClient.api.approvals).mockResolvedValue([]);
    jest.mocked(apiClient.api.workspaces).mockResolvedValue([mkWorkspace()]);
    jest.mocked(apiClient.api.ticketTree).mockResolvedValue([]);
  });

  const renderDashboard = () =>
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/"]}>
          <RouterBridgeSync />
          <Routes>
            <Route path="/" element={<Dashboard />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

  const openImportModal = async () => {
    const importButton = await screen.findByRole("button", { name: /^import$/i });
    await userEvent.click(importButton);
    await screen.findByTestId("mock-file-explorer");
  };

  const selectSmartModeAndFile = async () => {
    const group = screen.getByRole("radiogroup", { name: /import mode/i });
    const smartOption = within(group).getByRole("radio", { name: /^smart import$/i });
    await userEvent.click(smartOption);
    await userEvent.click(screen.getByTestId("toggle-features/auth.md"));
  };

  describe("DI1-DI5: Navigation Routing", () => {
    it("DI1/DI2: smart import creates a preview Studio session and navigates there, without showing the confirm modal", async () => {
      jest.mocked(apiClient.api.previewTicketImportPaths).mockResolvedValue(samplePreview());
      jest.mocked(apiClient.api.createTicketStudioSession).mockResolvedValue({
        id: "session-1",
      } as apiClient.TicketStudioSession);

      renderDashboard();
      await openImportModal();
      await selectSmartModeAndFile();
      await userEvent.click(screen.getByRole("button", { name: /continue/i }));

      await waitFor(() => {
        expect(apiClient.api.createTicketStudioSession).toHaveBeenCalledWith(
          expect.objectContaining({
            workspace_slug: "loregarden",
            is_preview: true,
          }),
        );
      });
      await waitFor(() => {
        expect(navigateToStudioTicketSession).toHaveBeenCalledWith("session-1");
      });
      expect(screen.queryByRole("dialog", { name: /import work items/i })).not.toBeInTheDocument();
    });

    it("DI3/DI4: regular import shows the confirmation modal and does not navigate to Studio", async () => {
      jest.mocked(apiClient.api.previewTicketImportPaths).mockResolvedValue(samplePreview());

      renderDashboard();
      await openImportModal();
      await userEvent.click(screen.getByTestId("toggle-features/auth.md"));
      await userEvent.click(screen.getByRole("button", { name: /continue/i }));

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /^import 1 item$/i })).toBeInTheDocument();
      });
      expect(apiClient.api.createTicketStudioSession).not.toHaveBeenCalled();
      expect(navigateToStudioTicketSession).not.toHaveBeenCalled();
    });
  });

  describe("DI6-DI8: Data Flow & Context", () => {
    it("DI6/DI7: smart import passes all parsed tickets into the Studio session's imported_tickets", async () => {
      const preview = samplePreview({
        tickets: [
          { title: "Feature One", work_item_type: "feature", external_id: "f1" },
          { title: "Feature Two", work_item_type: "feature", external_id: "f2" },
        ],
        total: 2,
      });
      jest.mocked(apiClient.api.previewTicketImportPaths).mockResolvedValue(preview);
      jest.mocked(apiClient.api.createTicketStudioSession).mockResolvedValue({
        id: "session-2",
      } as apiClient.TicketStudioSession);

      renderDashboard();
      await openImportModal();
      await selectSmartModeAndFile();
      await userEvent.click(screen.getByRole("button", { name: /continue/i }));

      await waitFor(() => {
        expect(apiClient.api.createTicketStudioSession).toHaveBeenCalledWith(
          expect.objectContaining({
            imported_tickets: preview.tickets,
          }),
        );
      });
    });

    it("DI10: smart import uses the workspace the picker was opened for", async () => {
      jest.mocked(apiClient.api.workspaces).mockResolvedValue([mkWorkspace({ slug: "other-ws" })]);
      jest.mocked(apiClient.api.previewTicketImportPaths).mockResolvedValue(samplePreview());
      jest.mocked(apiClient.api.createTicketStudioSession).mockResolvedValue({
        id: "session-3",
      } as apiClient.TicketStudioSession);

      renderDashboard();
      await openImportModal();
      await selectSmartModeAndFile();
      await userEvent.click(screen.getByRole("button", { name: /continue/i }));

      await waitFor(() => {
        expect(apiClient.api.createTicketStudioSession).toHaveBeenCalledWith(
          expect.objectContaining({ workspace_slug: "other-ws" }),
        );
      });
    });
  });

  describe("DI9: Error Handling", () => {
    it("DI9: smart import with no parsable tickets shows an error in the picker and does not navigate", async () => {
      jest.mocked(apiClient.api.previewTicketImportPaths).mockResolvedValue(
        samplePreview({ tickets: [], total: 0, errors: ["Could not parse selected file."] }),
      );

      renderDashboard();
      await openImportModal();
      await selectSmartModeAndFile();
      await userEvent.click(screen.getByRole("button", { name: /continue/i }));

      await waitFor(() => {
        expect(screen.getByText("Could not parse selected file.")).toBeInTheDocument();
      });
      expect(apiClient.api.createTicketStudioSession).not.toHaveBeenCalled();
      expect(navigateToStudioTicketSession).not.toHaveBeenCalled();
    });
  });
});
