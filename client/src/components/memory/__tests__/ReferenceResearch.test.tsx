import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api } from "../../../api/client";
import type { LibraryDetail } from "../../../api/referenceLibraryApi";
import type { ResearchProject } from "../../../api/referenceResearchApi";
import { useUiStore } from "../../../state/uiStore";
import { ReferenceResearchButton } from "../ReferenceResearch";
import { ReferenceSourceCapabilities } from "../ReferenceSourceCapabilities";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function renderWithRoutes(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/memory/library"]}>
        <Routes>
          <Route path="/memory/library" element={ui} />
          <Route path="/chat" element={<p>chat page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.resetAllMocks();
  useUiStore.setState({ chatScope: "", baxterChatSessionId: "" });
});

describe("the library's Research", () => {
  it("lists the workspace's questions and opens one's thread full-page", async () => {
    mockApi.researchQuestions.mockResolvedValue([
      { id: "q1", question: "Which repos auto-rig a mesh?", thread_id: "t1", capabilities: ["rigs"],
        run_count: 2, last_run_at: "2026-11-11T10:00:00Z", candidates: 4 },
    ]);  // prettier-ignore
    renderWithRoutes(<ReferenceResearchButton workspace="blobert" />);

    fireEvent.click(screen.getByRole("button", { name: "Research" }));
    const open = await screen.findByRole("button", { name: "Which repos auto-rig a mesh?" });
    expect(screen.getByText(/4 candidates · run 2/)).toBeInTheDocument();
    expect(mockApi.researchQuestions).toHaveBeenCalledWith("blobert");
    fireEvent.click(open);

    expect(await screen.findByText("chat page")).toBeInTheDocument();
    expect(useUiStore.getState()).toMatchObject({ chatScope: "blobert", baxterChatSessionId: "t1" });
  });

  it("starts a new thread in research mode and opens it", async () => {
    mockApi.researchQuestions.mockResolvedValue([]);
    mockApi.createBaxterChatSession.mockResolvedValue({ id: "t9" } as Awaited<ReturnType<typeof api.createBaxterChatSession>>);
    mockApi.setBaxterChatMode.mockResolvedValue({ id: "t9" } as Awaited<ReturnType<typeof api.setBaxterChatMode>>);
    renderWithRoutes(<ReferenceResearchButton workspace="blobert" />);

    fireEvent.click(screen.getByRole("button", { name: "Research" }));
    expect(await screen.findByText(/No research questions yet/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "New research thread" }));

    expect(await screen.findByText("chat page")).toBeInTheDocument();
    expect(mockApi.setBaxterChatMode).toHaveBeenCalledWith("blobert", "t9", "research");
    expect(useUiStore.getState().baxterChatSessionId).toBe("t9");
  });

  it("names a failed start and stays where it was", async () => {
    mockApi.researchQuestions.mockResolvedValue([]);
    mockApi.createBaxterChatSession.mockRejectedValue(new Error("database is locked"));
    renderWithRoutes(<ReferenceResearchButton workspace="blobert" />);
    fireEvent.click(screen.getByRole("button", { name: "Research" }));
    fireEvent.click(await screen.findByRole("button", { name: "New research thread" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("database is locked");
    expect(screen.queryByText("chat page")).not.toBeInTheDocument();
  });
});

describe("a source's Capabilities", () => {
  const detail = { source: { id: "src-1", url: "https://github.com/acme/rigger", name: "rigger" } } as LibraryDetail;
  const project: ResearchProject = {
    url: "https://github.com/acme/rigger", name: "Rigger", source_id: "src-1", licence_name: "", language: "",
    stars: null, last_commit_at: null, questions: [{ id: "q1", question: "Which repos auto-rig a mesh?", thread_id: "t1" }],
    claims: [{ id: "c1", capability: "rigs", status: "verified", evidence_path: "a.py", evidence_commit: "0123456789",
      evidence_start: 1, evidence_end: 2, evidence_excerpt: "x", evidence_url: "", note: "", recorded_by: "agent",
      created_at: "2026-11-11T10:00:00Z" }],
  };  // prettier-ignore

  it("shows what research found, without a queue button on the library's own page", async () => {
    mockApi.researchProject.mockResolvedValue(project);
    renderWithRoutes(<ReferenceSourceCapabilities detail={detail} />);
    expect(await screen.findByRole("heading", { name: /Capabilities/ })).toBeInTheDocument();
    expect(screen.getByText(/From research: “Which repos auto-rig a mesh\?”/)).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "What research found about Rigger" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "In library" })).not.toBeInTheDocument();
  });

  it("is absent for a source research never looked at", async () => {
    mockApi.researchProject.mockResolvedValue({ ...project, claims: [], questions: [] });
    renderWithRoutes(<ReferenceSourceCapabilities detail={detail} />);
    await waitFor(() => expect(mockApi.researchProject).toHaveBeenCalled());
    expect(screen.queryByRole("heading", { name: /Capabilities/ })).not.toBeInTheDocument();
  });
});
