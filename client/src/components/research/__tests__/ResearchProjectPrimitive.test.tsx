import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../../api/client";
import type { ResearchClaim, ResearchProject } from "../../../api/referenceResearchApi";
import { ResearchProjectPrimitive } from "../../chat/primitives/ResearchProjectPrimitive";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;
const RIGGER = "https://github.com/acme/rigger";

function claim(capability: string, extra: Partial<ResearchClaim>): ResearchClaim {
  return {
    id: capability, capability, status: "docs_only", evidence_path: "", evidence_commit: "",
    evidence_start: 0, evidence_end: 0, evidence_excerpt: "", evidence_url: "", note: "",
    recorded_by: "agent", created_at: "2026-11-11T10:00:00Z", ...extra,
  };  // prettier-ignore
}

const PROJECT: ResearchProject = {
  url: RIGGER, name: "Rigger", source_id: null, licence_name: "MIT", language: "Python", stars: 412,
  last_commit_at: "2026-10-30T12:00:00Z",
  claims: [
    claim("computes skin weights", {
      status: "verified", evidence_path: "rig/weights.py", evidence_commit: "0123456789abcdef",
      evidence_start: 4, evidence_end: 9, evidence_url: `${RIGGER}/blob/0123456789abcdef/rig/weights.py#L4-L9`,
    }),
    claim("generates a skeleton", { status: "refuted", recorded_by: "operator", note: "it only retargets" }),
  ],
  questions: [{ id: "q1", question: "Which repos auto-rig a mesh?", thread_id: "t1" }],
};  // prettier-ignore

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ResearchProjectPrimitive part={{ primitive: "research_project", url: RIGGER }} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => jest.resetAllMocks());

it("lists every claim with its evidence, and offers no refutation over a person's", async () => {
  mockApi.researchProject.mockResolvedValue(PROJECT);
  renderCard();

  const table = await screen.findByRole("table", { name: "What research found about Rigger" });
  expect(screen.getByText("2 claims · 1 verified · 1 question")).toBeInTheDocument();
  expect(within(table).getByRole("link", { name: "rig/weights.py:4–9 @ 0123456789ab" })).toHaveAttribute(
    "href",
    PROJECT.claims[0].evidence_url,
  );
  expect(within(table).getByRole("button", { name: "Refute: Rigger computes skin weights" })).toBeInTheDocument();
  expect(within(table).queryByRole("button", { name: "Refute: Rigger generates a skeleton" })).not.toBeInTheDocument();
  expect(mockApi.researchProject).toHaveBeenCalledWith(RIGGER);
});

it("queues a project that is not in the library, under its latest question", async () => {
  mockApi.researchProject.mockResolvedValue(PROJECT);
  mockApi.referenceAreas.mockResolvedValue([]);
  mockApi.queueResearchPicks.mockResolvedValue([
    { url: RIGGER, outcome: "queued", kind: "repo", source: null, detail: "", initiatives_linked: [] },
  ]);
  renderCard();

  fireEvent.click(await screen.findByRole("button", { name: "Queue for review…" }));
  fireEvent.click(await screen.findByRole("button", { name: "Queue 1 for review" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(mockApi.queueResearchPicks).toHaveBeenCalledWith({ urls: [RIGGER], question_id: "q1", areas: [] });
});

it("links to the library entry instead of offering a queue once it is there", async () => {
  mockApi.researchProject.mockResolvedValue({ ...PROJECT, source_id: "src-1" });
  renderCard();
  expect(await screen.findByRole("link", { name: "In library" })).toHaveAttribute("href", "/memory/library/src-1");
  expect(screen.queryByRole("button", { name: "Queue for review…" })).not.toBeInTheDocument();
});

it("says research has not looked yet, rather than showing an empty table", async () => {
  mockApi.researchProject.mockResolvedValue({ ...PROJECT, claims: [], questions: [] });
  renderCard();
  expect(await screen.findByText(/Research has not checked anything about this project yet/)).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});
