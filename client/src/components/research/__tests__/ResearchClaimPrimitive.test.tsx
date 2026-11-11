import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api } from "../../../api/client";
import type { ResearchClaim, ResearchClaimCard } from "../../../api/referenceResearchApi";
import { ResearchClaimPrimitive } from "../../chat/primitives/ResearchClaimPrimitive";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;
const RIGGER = "https://github.com/acme/rigger";
const SKELETON = "generates a skeleton for an unrigged mesh";
const COMMIT = "0123456789abcdef0123456789abcdef01234567";

const VERIFIED: ResearchClaim = {
  id: "c1", capability: SKELETON, status: "verified", evidence_path: "rig/auto_skeleton.py",
  evidence_commit: COMMIT, evidence_start: 10, evidence_end: 11,
  evidence_excerpt: "def build_skeleton(mesh):\n    return joints(mesh)",
  evidence_url: `${RIGGER}/blob/${COMMIT}/rig/auto_skeleton.py#L10-L11`,
  note: "build_skeleton places joints", recorded_by: "agent", created_at: "2026-11-11T10:00:00Z",
};  // prettier-ignore

function card(extra: Partial<ResearchClaimCard> = {}): ResearchClaimCard {
  return { url: RIGGER, capability: SKELETON, name: "Rigger", claim: VERIFIED, earlier: 1, ...extra };
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ResearchClaimPrimitive part={{ primitive: "research_claim", url: RIGGER, capability: SKELETON }} />
    </QueryClientProvider>,
  );
}

beforeEach(() => jest.resetAllMocks());

it("shows the cited lines the server read, numbered, linked to the file at the commit", async () => {
  mockApi.researchClaim.mockResolvedValue(card());
  renderCard();

  const link = await screen.findByRole("link", { name: /rig\/auto_skeleton\.py:10–11 @ 0123456789ab/ });
  expect(link).toHaveAttribute("href", VERIFIED.evidence_url);
  const excerpt = screen.getByLabelText("Lines 10 to 11 of rig/auto_skeleton.py");
  expect(excerpt).toHaveTextContent("10def build_skeleton(mesh):");
  expect(excerpt).toHaveTextContent("11 return joints(mesh)");
  expect(screen.getByText(/1 earlier claim$/)).toBeInTheDocument();
  expect(mockApi.researchClaim).toHaveBeenCalledWith(RIGGER, SKELETON);
});

it("says nothing is recorded yet, rather than showing an empty verdict", async () => {
  mockApi.researchClaim.mockResolvedValue(card({ claim: null, earlier: 0, name: "" }));
  renderCard();
  expect(await screen.findByText(/Nothing is recorded for this yet/)).toBeInTheDocument();
});

it("keeps the reason and says why when a refutation is not recorded, then records it", async () => {
  mockApi.researchClaim.mockResolvedValue(card());
  mockApi.refuteResearchClaim.mockRejectedValueOnce(new Error("database is locked"));
  mockApi.refuteResearchClaim.mockResolvedValueOnce({ ...VERIFIED, id: "c2", status: "refuted", recorded_by: "operator" });
  renderCard();

  fireEvent.click(await screen.findByRole("button", { name: "Refute…" }));
  const submit = screen.getByRole("button", { name: "Record refutation" });
  expect(submit).toBeDisabled();
  const reason = screen.getByLabelText(/Why/);
  fireEvent.change(reason, { target: { value: "it only retargets an existing rig" } });
  fireEvent.click(submit);

  expect(await screen.findByRole("alert")).toHaveTextContent("database is locked");
  expect(reason).toHaveValue("it only retargets an existing rig");

  fireEvent.click(screen.getByRole("button", { name: "Record refutation" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Record refutation" })).not.toBeInTheDocument());
  expect(mockApi.refuteResearchClaim).toHaveBeenLastCalledWith({
    url: RIGGER,
    capability: SKELETON,
    note: "it only retargets an existing rig",
    question_id: null,
  });
});

it("offers no second refutation once a person has refuted it", async () => {
  mockApi.researchClaim.mockResolvedValue(
    card({ claim: { ...VERIFIED, status: "refuted", recorded_by: "operator", evidence_excerpt: "" } }),
  );
  renderCard();
  expect(await screen.findByText("Recorded by a person", { exact: false })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Refute…" })).not.toBeInTheDocument();
});
