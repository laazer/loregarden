import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import { StudioChatMessages } from "../../studio/StudioChat";
import type { ChatMessageView } from "../chatUtils";

jest.mock("../../../api/client", () => jest.requireActual("../../../test/apiClientMock"));

function renderThread(messages: ChatMessageView[]): ReturnType<typeof render> {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <QueryClientProvider client={qc}>
      <StudioChatMessages messages={messages} assistantLabel="Baxter" />
    </QueryClientProvider>
  );
  return render(ui);
}

/** A turn carrying only a card — an aside, a gate, a ticket — and no prose. */
function cardOnlyTurn(): ChatMessageView {
  return {
    id: "m1",
    role: "assistant",
    content: "The log shows it shelling out to git.",
    created_at: "2026-09-10T10:00:00",
    parts: [
      {
        primitive: "btw",
        exchange_id: "bx1",
        ticket_id: "t1",
        question: "why the subprocess path?",
        answer: "The log shows it shelling out to git.",
        observed_run_id: "run-1",
        observed_agent_id: "planner",
        observed_stage_key: "plan",
        escalated: false,
        interactive: false,
      },
    ],
  } as ChatMessageView;
}

describe("an assistant turn that is only a card", () => {
  it("puts the card in the avatar's own row rather than a row below it", () => {
    // The row used to render a bare `flex: 1` spacer whenever there was no text
    // part, stacking the card underneath — which left the avatar hanging a whole
    // row above the only thing it labels.
    const { container } = renderThread([cardOnlyTurn()]);

    const row = container.querySelector(".lg-chat-assistant-row");
    expect(row).not.toBeNull();
    expect(row?.querySelector(".lg-chat-assistant-cards")).not.toBeNull();
    expect(container.querySelectorAll(".lg-primitive-card").length).toBe(1);
  });

  it("leaves a turn with prose alone — the card still follows the text", () => {
    const turn = cardOnlyTurn();
    turn.parts = [{ primitive: "text", content: "Here is what I found." }, ...(turn.parts ?? [])];

    const { container } = renderThread([turn]);

    const row = container.querySelector(".lg-chat-assistant-row");
    expect(row?.querySelector(".lg-chat-reply")).not.toBeNull();
    expect(row?.querySelector(".lg-chat-assistant-cards")).toBeNull();
    // Still rendered, just below the prose rather than inside the row.
    expect(container.querySelectorAll(".lg-primitive-card").length).toBe(1);
  });
});
