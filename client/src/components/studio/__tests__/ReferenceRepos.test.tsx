import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ReferenceReposSection } from "../ReferenceRepos";
import type { ReferenceRepo, TicketStudioSession, TicketStudioSurveyFinding } from "../../../api/types";

jest.mock("../../../api/client", () => {
  const originalClient = jest.requireActual("../../../api/client");
  return {
    ...originalClient,
    api: {
      ...originalClient.api,
      generateTicketStudioSurvey: jest.fn(),
      saveTicketStudioSurvey: jest.fn(),
      setTicketStudioReferenceRepos: jest.fn(),
    },
  };
});

const { api } = require("../../../api/client");

const REPO: ReferenceRepo = {
  id: "repo-1",
  workspace_slug: "loregarden",
  url: "https://github.com/nousresearch/hermes-agent",
  slug: "github.com/nousresearch/hermes-agent",
  name: "hermes-agent",
  local_path: "/cache/github.com/nousresearch/hermes-agent",
  default_branch: "main",
  head_sha: "abc123",
  notes: "",
  cloned: true,
  last_synced_at: null,
  created_at: "2026-07-27T00:00:00Z",
  updated_at: "2026-07-27T00:00:00Z",
};

const FINDINGS: TicketStudioSurveyFinding[] = [
  {
    ref: "find-1",
    title: "Skill creation loop",
    repo_slug: REPO.slug,
    source_paths: ["skills/"],
    what_it_gives: "Agents write their own skills",
    fit: "Next to our learnings service",
    risks: "",
    verdict: "adapt",
    effort: "M",
    selected: true,
  },
  {
    ref: "find-2",
    title: "Telegram gateway",
    repo_slug: REPO.slug,
    source_paths: ["gateway/telegram.py"],
    what_it_gives: "Chat over Telegram",
    fit: "",
    risks: "",
    verdict: "skip",
    effort: "L",
    selected: false,
  },
];

function makeSession(overrides: Partial<TicketStudioSession> = {}): TicketStudioSession {
  return {
    id: "session-1",
    workspace_slug: "loregarden",
    title: "Adopt a learning loop",
    brief: "",
    parent_ticket_id: null,
    parent_ticket_title: "",
    status: "draft",
    summary: "",
    clarifying_questions: [],
    clarifying_answers: [],
    clarifying_resolved: true,
    draft: [],
    messages: [],
    runtime: { cli_adapter: "", claude_model: "", cursor_model: "", lmstudio_base_url: "", lmstudio_model: "" },
    is_preview: false,
    imported_tickets: [],
    reference_repos: [REPO],
    survey: FINDINGS,
    created_at: "2026-07-27T00:00:00Z",
    updated_at: "2026-07-27T00:00:00Z",
    ...overrides,
  } as TicketStudioSession;
}

function renderSection(session: TicketStudioSession) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ReferenceReposSection session={session} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
});

test("renders nothing when no reference repo is attached", () => {
  renderSection(makeSession({ reference_repos: [], survey: [] }));
  expect(screen.queryByTestId("reference-repos-section")).not.toBeInTheDocument();
});

test("lists the attached repo and its survey findings", () => {
  renderSection(makeSession());
  expect(screen.getByText(REPO.slug)).toBeInTheDocument();
  expect(screen.getByText("Skill creation loop")).toBeInTheDocument();
  expect(screen.getByText("Telegram gateway")).toBeInTheDocument();
  expect(screen.getByText(/1 of 2 parts selected/)).toBeInTheDocument();
});

test("toggling a finding persists the new selection", async () => {
  const updated = makeSession({
    survey: FINDINGS.map((finding) =>
      finding.ref === "find-2" ? { ...finding, selected: true } : finding,
    ),
  });
  api.saveTicketStudioSurvey.mockResolvedValue(updated);

  renderSection(makeSession());
  await userEvent.click(screen.getByLabelText("Include Telegram gateway"));

  await waitFor(() => expect(api.saveTicketStudioSurvey).toHaveBeenCalledTimes(1));
  const [sessionId, findings] = api.saveTicketStudioSurvey.mock.calls[0];
  expect(sessionId).toBe("session-1");
  expect(findings.find((f: TicketStudioSurveyFinding) => f.ref === "find-2").selected).toBe(true);
  expect(findings.find((f: TicketStudioSurveyFinding) => f.ref === "find-1").selected).toBe(true);
});

test("surfaces a failed survey instead of silently showing no findings", async () => {
  api.generateTicketStudioSurvey.mockRejectedValue(
    new Error(JSON.stringify({ detail: "Attach at least one cloned reference repo before surveying" })),
  );

  renderSection(makeSession({ survey: [] }));
  await userEvent.click(screen.getByRole("button", { name: /survey what's useful/i }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Attach at least one cloned reference repo before surveying",
  );
});
