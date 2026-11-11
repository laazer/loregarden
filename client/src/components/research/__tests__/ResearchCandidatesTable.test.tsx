import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../../api/client";
import type { ResearchCandidate, ResearchClaim, ResearchQuestion } from "../../../api/referenceResearchApi";
import { ResearchCandidatesPrimitive } from "../../chat/primitives/ResearchCandidatesPrimitive";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;
const SKELETON = "generates a skeleton for an unrigged mesh";
const WEIGHTS = "computes skin weights";

function claim(capability: string, status: ResearchClaim["status"], extra: Partial<ResearchClaim> = {}): ResearchClaim {
  return {
    id: `${capability}-${status}`, capability, status, evidence_path: "", evidence_commit: "",
    evidence_start: 0, evidence_end: 0, evidence_excerpt: "", evidence_url: "", note: "", recorded_by: "agent", created_at: "2026-11-11T10:00:00Z", ...extra,
  };  // prettier-ignore
}

function candidate(name: string, extra: Partial<ResearchCandidate>): ResearchCandidate {
  return {
    id: name, url: `https://github.com/acme/${name.toLowerCase()}`, name, summary: "", source_id: null,
    licence_name: "MIT", language: "Python", stars: 10, last_commit_at: "2026-10-01T00:00:00Z",
    metadata_error: "", new: false, claims: {}, ...extra,
  };  // prettier-ignore
}

const QUESTION: ResearchQuestion = {
  id: "q1",
  thread_id: "t1",
  question: "Which repos auto-rig a mesh?",
  capabilities: [SKELETON, WEIGHTS],
  run_count: 2,
  last_run_at: "2026-11-11T10:00:00Z",
  saved: false,
  candidates: [
    candidate("AnyCreature", {
      stars: 900,
      claims: {
        [SKELETON]: claim(SKELETON, "refuted", { recorded_by: "operator", note: "it can't build skeletons" }),
        [WEIGHTS]: claim(WEIGHTS, "docs_only"),
      },
    }),
    candidate("Rigger", {
      stars: 40,
      new: true,
      source_id: "src-1",
      claims: {
        [SKELETON]: claim(SKELETON, "verified", {
          evidence_path: "rig/auto_skeleton.py",
          evidence_commit: "abcdef1234567890",
          evidence_url: "https://github.com/acme/rigger/blob/abcdef1234567890/rig/auto_skeleton.py",
          note: "build_skeleton places joints",
        }),
        [WEIGHTS]: null,
      },
    }),
  ],
};

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ResearchCandidatesPrimitive part={{ primitive: "research_candidates", question_id: "q1" }} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function projectColumn(table: HTMLElement) {
  return within(table)
    .getAllByRole("rowheader")
    .map((cell) => cell.querySelector("a")?.textContent);
}

describe("ResearchCandidatesPrimitive", () => {
  beforeEach(() => jest.clearAllMocks());

  it("shows the recorded claims per capability, best evidence first, with a link to the file read", async () => {
    mockApi.researchQuestion.mockResolvedValue(QUESTION);
    renderCard();

    const table = await screen.findByRole("table", { name: "Candidates for: Which repos auto-rig a mesh?" });
    expect(mockApi.researchQuestion).toHaveBeenCalledWith("q1");
    expect(screen.getByText("2 candidates · 0 verified for everything asked · run 2")).toBeInTheDocument();
    // Sorted by the first capability, best evidence first: verified above refuted.
    expect(projectColumn(table)).toEqual(["Rigger", "AnyCreature"]);
    const evidence = within(table).getByRole("link", { name: /^Verified: generates a skeleton/ });
    expect(evidence).toHaveAttribute("href", QUESTION.candidates[1].claims[SKELETON]?.evidence_url);
    expect(evidence).toHaveAttribute("title", "build_skeleton places joints · rig/auto_skeleton.py @ abcdef123456");
    expect(within(table).getByText("Refuted")).toHaveAttribute(
      "title",
      "it can't build skeletons · A person said so",
    );
    expect(within(table).getByText("Not checked")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "In library" })).toHaveAttribute("href", "/memory/library/src-1");
    expect(within(table).getByText("New")).toBeInTheDocument();
  });

  it("sorts by a column and says so to assistive tech", async () => {
    mockApi.researchQuestion.mockResolvedValue(QUESTION);
    renderCard();
    const table = await screen.findByRole("table");

    const stars = within(table).getByRole("button", { name: "Stars" });
    fireEvent.click(stars);
    expect(stars.closest("th")).toHaveAttribute("aria-sort", "descending");
    expect(projectColumn(table)).toEqual(["AnyCreature", "Rigger"]);
    fireEvent.click(stars);
    expect(stars.closest("th")).toHaveAttribute("aria-sort", "ascending");
    expect(projectColumn(table)).toEqual(["Rigger", "AnyCreature"]);
  });

  it("queues the selected projects for review with the question, and leaves library ones unselectable", async () => {
    mockApi.researchQuestion.mockResolvedValue(QUESTION);
    mockApi.referenceAreas.mockResolvedValue([]);
    mockApi.queueResearchPicks.mockResolvedValue([
      { url: "https://github.com/acme/anycreature", outcome: "queued", kind: "repo", source: null, detail: "", initiatives_linked: [] },
    ]);  // prettier-ignore
    renderCard();
    await screen.findByRole("table");

    const queue = screen.getByRole("button", { name: /Queue\s+for review/ });
    expect(queue).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Rigger is already in the library" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select AnyCreature" }));
    fireEvent.click(screen.getByRole("button", { name: "Queue 1 for review…" }));

    expect(await screen.findByRole("dialog")).toHaveTextContent("Which repos auto-rig a mesh?");
    fireEvent.click(screen.getByRole("button", { name: "Queue 1 for review" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(mockApi.queueResearchPicks).toHaveBeenCalledWith({
      urls: ["https://github.com/acme/anycreature"],
      question_id: "q1",
      areas: [],
    });
    expect(screen.getByRole("checkbox", { name: "Select AnyCreature" })).not.toBeChecked();
  });

  it("keeps the dialog open and names each project that was not queued", async () => {
    mockApi.researchQuestion.mockResolvedValue(QUESTION);
    mockApi.referenceAreas.mockResolvedValue([]);
    mockApi.queueResearchPicks.mockResolvedValue([
      { url: "https://github.com/acme/anycreature", outcome: "failed", kind: "repo", source: null, detail: "refused: licence", initiatives_linked: [] },
    ]);  // prettier-ignore
    renderCard();
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("checkbox", { name: "Select AnyCreature" }));
    fireEvent.click(screen.getByRole("button", { name: "Queue 1 for review…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Queue 1 for review" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("https://github.com/acme/anycreature: refused: licence");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("says when nothing was recorded, and names a failed load", async () => {
    mockApi.researchQuestion.mockResolvedValueOnce({ ...QUESTION, candidates: [] });
    renderCard();
    expect(await screen.findByText(/No candidates recorded for this question yet/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("names a failed load instead of showing an empty table", async () => {
    mockApi.researchQuestion.mockRejectedValue(new Error("No research question q1"));
    renderCard();
    expect(await screen.findByText(/Couldn't load this research: No research question q1/)).toBeInTheDocument();
  });
});
