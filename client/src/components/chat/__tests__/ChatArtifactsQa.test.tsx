import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { ChatArtifactsList } from "../ChatArtifactsList";
import type { ChatMessageView } from "../chatUtils";
import { primitiveHistory } from "../primitiveHistory";

/**
 * The "Made in this chat" panel renders the same `qa` cards the thread does,
 * from the same messages, so a card the operator already answered in prose must
 * be read-only here too — the Living Map records it as settled either way.
 */
function thread(): ChatMessageView[] {
  return [
    {
      id: "a1",
      role: "assistant",
      content: "",
      parts: [
        {
          primitive: "qa",
          interactive: true,
          items: [{ id: "scope", question: "Which users are in scope?" }],
        },
      ],
    },
    { id: "u1", role: "user", content: "Just the admins for now.", parts: [] },
  ];
}

function renderArtifacts(messages: ChatMessageView[]) {
  return render(
    <MemoryRouter>
      <ChatArtifactsList
        entries={primitiveHistory(messages)}
        onShowInChat={() => {}}
        onSubmit={() => {}}
      />
    </MemoryRouter>,
  );
}

describe("ChatArtifactsList qa cards", () => {
  it("shows a question answered in the thread as read-only, not as needing you", () => {
    renderArtifacts(thread());

    expect(screen.queryByText(/Needs you/)).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /send answer/i })).not.toBeInTheDocument();
    expect(screen.getByText("Just the admins for now.")).toBeInTheDocument();
  });

  it("leaves a question with no later user reply answerable", () => {
    renderArtifacts([thread()[0]]);

    expect(screen.getByText("Needs you")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send answer/i })).toBeInTheDocument();
  });
});
