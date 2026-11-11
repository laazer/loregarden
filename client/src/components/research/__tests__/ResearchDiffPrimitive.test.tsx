import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../../api/client";
import type { ResearchCandidate, ResearchClaim, ResearchRunDiff } from "../../../api/referenceResearchApi";
import { ResearchDiffPrimitive } from "../../chat/primitives/ResearchDiffPrimitive";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;
const SKELETON = "generates a skeleton for an unrigged mesh";

function claim(status: ResearchClaim["status"], extra: Partial<ResearchClaim> = {}): ResearchClaim {
  return {
    id: status, capability: SKELETON, status, evidence_path: "", evidence_commit: "", evidence_start: 0,
    evidence_end: 0, evidence_excerpt: "", evidence_url: "", note: "", recorded_by: "agent",
    created_at: "2026-11-11T10:00:00Z", ...extra,
  };  // prettier-ignore
}

function candidate(name: string, extra: Partial<ResearchCandidate> = {}): ResearchCandidate {
  return {
    id: name, url: `https://github.com/acme/${name.toLowerCase()}`, name, summary: "", source_id: null,
    licence_name: "", language: "", stars: null, last_commit_at: null, metadata_error: "", new: true,
    claims: {}, ...extra,
  };  // prettier-ignore
}

const DIFF: ResearchRunDiff = {
  question_id: "q1",
  question: "Which repos auto-rig a mesh?",
  run: 2,
  previous_run: 1,
  dropped_known: true,
  added: [candidate("Newcomer"), candidate("Shelved", { source_id: "src-9" })],
  dropped: [candidate("Other")],
  changed: [
    {
      url: "https://github.com/acme/rigger",
      name: "Rigger",
      capability: SKELETON,
      before: claim("docs_only"),
      after: claim("not_found", { note: "no such code" }),
    },
  ],
  unchanged: 3,
};

function renderCard(run?: number) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ResearchDiffPrimitive part={{ primitive: "research_diff", question_id: "q1", run }} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ResearchDiffPrimitive", () => {
  beforeEach(() => jest.clearAllMocks());

  it("shows what the run added, dropped and changed, read from the record", async () => {
    mockApi.researchRunDiff.mockResolvedValue(DIFF);
    renderCard();

    const table = await screen.findByRole("table", { name: "Claims that changed in run 2" });
    expect(mockApi.researchRunDiff).toHaveBeenCalledWith("q1", undefined);
    expect(screen.getByText("Run 2 vs 1 · 2 new · 1 gone · 1 changed")).toBeInTheDocument();
    const row = within(table).getByRole("row", { name: /Rigger/ });
    expect(within(row).getByText("Docs only")).toBeInTheDocument();
    expect(within(row).getByText("no such code")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Projects new this run" })).getAllByRole("listitem")).toHaveLength(2);
    expect(within(screen.getByRole("list", { name: "Projects gone this run" })).getByText("Other")).toBeInTheDocument();
  });

  it("queues only the new projects that are not already in the library", async () => {
    mockApi.researchRunDiff.mockResolvedValue(DIFF);
    mockApi.referenceAreas.mockResolvedValue([]);
    mockApi.queueResearchPicks.mockResolvedValue([
      { url: "https://github.com/acme/newcomer", outcome: "queued", kind: "repo", source: null, detail: "", initiatives_linked: [] },
    ]);  // prettier-ignore
    renderCard();

    fireEvent.click(await screen.findByRole("button", { name: "Queue 1 new for review…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Queue 1 for review" }));
    await waitFor(() =>
      expect(mockApi.queueResearchPicks).toHaveBeenCalledWith({
        urls: ["https://github.com/acme/newcomer"],
        question_id: "q1",
        areas: [],
      }),
    );
  });

  it("says plainly when nothing changed, and when an older run cannot say what dropped", async () => {
    mockApi.researchRunDiff.mockResolvedValue({ ...DIFF, added: [], dropped: [], changed: [], dropped_known: false });
    renderCard(2);

    expect(await screen.findByText("Nothing changed since run 1: 3 claims re-checked and found the same.")).toBeInTheDocument();
    expect(screen.getByText(/which projects it dropped is no longer known/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(mockApi.researchRunDiff).toHaveBeenCalledWith("q1", 2);
  });

  it("names a failed load", async () => {
    mockApi.researchRunDiff.mockRejectedValue(new Error("This question has not been run yet"));
    renderCard();
    expect(await screen.findByText(/Couldn't load what this run changed: This question has not been run yet/)).toBeInTheDocument();
  });
});
