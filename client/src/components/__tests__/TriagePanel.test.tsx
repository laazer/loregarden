import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api } from "../../api/client";
import { TRIAGE_AGENT_NAME } from "../../lib/triageAgent";
import { TriagePanel } from "../TriagePanel";

jest.mock("../../api/client", () => {
  const actual = jest.requireActual("../../api/client");
  return {
    ...actual,
    api: {
      ...actual.api,
      triage: jest.fn(),
      approvals: jest.fn(),
      sendTriageMessage: jest.fn(),
      setTriageRuntime: jest.fn(),
    },
  };
});

const mockedApi = api as jest.Mocked<typeof api>;

const TICKET = {
  id: "t1",
  external_id: "LG-42",
  title: "Fix triage chat",
  branch: "feat/triage",
} as never;

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TriagePanel ticket={TICKET} runtimeOptions={undefined} />
    </QueryClientProvider>,
  );
}

describe("TriagePanel", () => {
  beforeEach(() => {
    mockedApi.triage.mockResolvedValue({
      messages: [],
      pending_approvals: [],
      recent_approvals: [],
      run_status: "idle",
      runtime: {
        cli_adapter: "default",
        claude_model: "",
        cursor_model: "",
        lmstudio_base_url: "",
        lmstudio_model: "",
      },
    } as never);
    mockedApi.approvals.mockResolvedValue([]);
  });

  it("renders the Baxter chat shell with hero composer when empty", async () => {
    renderPanel();
    expect(screen.getByText("LG-42 · feat/triage")).toBeInTheDocument();
    expect(screen.getAllByText(TRIAGE_AGENT_NAME).length).toBeGreaterThan(0);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /Good (morning|afternoon|evening)/i })).toBeInTheDocument();
    });
    expect(screen.getByLabelText("Ask Baxter")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("What should we look at on this ticket?")).toBeInTheDocument();
    expect(screen.getByText("What is blocking this ticket?")).toBeInTheDocument();
  });

  it("seeds the hero composer from a welcome chip", async () => {
    renderPanel();
    await waitFor(() => screen.getByText("What is blocking this ticket?"));
    fireEvent.click(screen.getByText("What is blocking this ticket?"));
    await waitFor(() => {
      expect(screen.getByPlaceholderText("What should we look at on this ticket?")).toHaveValue(
        "What is blocking this ticket?",
      );
    });
  });

  it("renders Baxter bubbles when the thread has turns", async () => {
    mockedApi.triage.mockResolvedValue({
      messages: [
        { id: "m1", role: "user", content: "What failed?" },
        { id: "m2", role: "assistant", content: "The last stage blocked on tests." },
      ],
      pending_approvals: [],
      recent_approvals: [],
      run_status: "idle",
      runtime: {
        cli_adapter: "default",
        claude_model: "",
        cursor_model: "",
        lmstudio_base_url: "",
        lmstudio_model: "",
      },
    } as never);

    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("What failed?")).toBeInTheDocument();
      expect(screen.getByText("The last stage blocked on tests.")).toBeInTheDocument();
    });
    expect(screen.queryByRole("heading", { name: /Good (morning|afternoon|evening)/i })).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("Reply to Baxter…")).toBeInTheDocument();
  });
});
