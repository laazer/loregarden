import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../../api/client";
import type { LibraryList } from "../../../api/referenceLibraryApi";
import { useToastStore } from "../../../state/toastStore";
import libraryFixture from "../../../test/fixtures/prod-shape/reference-library-loregarden.json";
import { ReferencePasteForm } from "../ReferencePasteForm";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;
const SOURCE = (libraryFixture as unknown as LibraryList).sources[0];

// The shape of the "tools worth bookmarking" post this form exists for.
const ARTICLE = `Here are 3 open-source tools worth bookmarking today:
1. Twenty – The Open-Source CRM Built for AI Agents
Replaces: Salesforce / HubSpot Enterprise ($150+/user/mo).
GitHub: https://github.com/twentyhq/twenty
2. CLI-Anything – Make Any Software Agent-Native
GitHub: https://github.com/HKUDS/CLI-Anything
3. Harper – Offline, Privacy-First AI Grammar & Writing Engine
GitHub: https://github.com/Automattic/harper`;

function renderForm(onClose = jest.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ReferencePasteForm workspace="loregarden" onClose={onClose} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return screen.getByRole("form", { name: "Paste a list of references" });
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
  mockApi.workspaces.mockResolvedValue([] as Awaited<ReturnType<typeof api.workspaces>>);
  mockApi.initiatives.mockResolvedValue([]);
});

describe("ReferencePasteForm", () => {
  it("turns a pasted article into named rows and queues each with its own reason", async () => {
    mockApi.addReferenceSources.mockResolvedValue([
      { url: "https://github.com/twentyhq/twenty", outcome: "queued", kind: "repo", source: SOURCE, detail: "", initiatives_linked: [] },
      { url: "https://github.com/Automattic/harper", outcome: "failed", kind: "repo", source: null, detail: "refused: GPL-3.0", initiatives_linked: [] },
    ]);  // prettier-ignore
    const form = renderForm();
    expect(within(form).getByRole("button", { name: "Queue for review" })).toBeDisabled();
    fireEvent.change(within(form).getByRole("textbox", { name: "Text with links" }), { target: { value: ARTICLE } });

    const table = within(form).getByRole("table", { name: "Links to queue" });
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(within(table).getByRole("textbox", { name: "Name for https://github.com/twentyhq/twenty" })).toHaveValue("Twenty");
    expect(within(table).getByRole("textbox", { name: "Why read https://github.com/HKUDS/CLI-Anything" })).toHaveValue(
      "Make Any Software Agent-Native",
    );

    fireEvent.change(within(table).getByRole("textbox", { name: "Name for https://github.com/twentyhq/twenty" }), {
      target: { value: "twenty-crm" },
    });
    fireEvent.click(within(table).getByRole("button", { name: "Drop https://github.com/HKUDS/CLI-Anything" }));
    fireEvent.click(within(form).getByRole("button", { name: "Queue 2 for review" }));

    await waitFor(() =>
      expect(mockApi.addReferenceSources).toHaveBeenCalledWith({
        workspace_slugs: ["loregarden"], kind: null, question: "", queued_reason: "", initiative_ids: [],
        urls: ["https://github.com/twentyhq/twenty", "https://github.com/Automattic/harper"],
        entries: [
          {
            url: "https://github.com/twentyhq/twenty", name: "twenty-crm",
            queued_reason: "The Open-Source CRM Built for AI Agents\nReplaces: Salesforce / HubSpot Enterprise ($150+/user/mo).",
          },
          {
            url: "https://github.com/Automattic/harper", name: "Harper",
            queued_reason: "Offline, Privacy-First AI Grammar & Writing Engine",
          },
        ],
      }),
    );  // prettier-ignore
    // The added one leaves the table; the failed one stays beside its reason.
    const results = await within(form).findByRole("list", { name: "What each URL did" });
    expect(within(results).getByText(/not added: refused: GPL-3.0/)).toBeInTheDocument();
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(within(table).getByRole("textbox", { name: "Name for https://github.com/Automattic/harper" })).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Done" })).toBeEnabled();
    fireEvent.click(within(table).getByRole("button", { name: "Drop https://github.com/Automattic/harper" }));
    expect(within(form).getByText(/Every link in this text has been added or dropped/)).toBeInTheDocument();
  });

  it("says when the paste holds no link, and sends nothing", () => {
    const form = renderForm();
    fireEvent.change(within(form).getByRole("textbox", { name: "Text with links" }), { target: { value: "no links here" } });
    expect(within(form).getByText("No http(s) link in what you pasted.")).toBeInTheDocument();
    expect(within(form).queryByRole("table")).not.toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Queue for review" })).toBeDisabled();
  });

  it("refuses more links than one bulk add takes", () => {
    const form = renderForm();
    const many = Array.from({ length: 51 }, (_, i) => `https://github.com/a/r${i}`).join("\n");
    fireEvent.change(within(form).getByRole("textbox", { name: "Text with links" }), { target: { value: many } });
    expect(within(form).getByText("51 links; at most 50 at a time. Drop some below.")).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Queue 51 for review" })).toBeDisabled();
  });

  it("keeps the rows and names the failure when the request fails", async () => {
    mockApi.addReferenceSources.mockRejectedValue(new Error("server down"));
    const form = renderForm();
    fireEvent.change(within(form).getByRole("textbox", { name: "Text with links" }), {
      target: { value: "https://github.com/a/one" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Queue 1 for review" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("Not added: server down");
    expect(within(form).getByRole("table", { name: "Links to queue" })).toBeInTheDocument();
    expect(useToastStore.getState().toasts[0]).toMatchObject({ tone: "error", title: "The references were not added" });
  });
});
