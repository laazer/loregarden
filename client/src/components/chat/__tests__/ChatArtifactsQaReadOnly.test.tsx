import { render, screen } from "@testing-library/react";

import { ChatArtifactsList } from "../ChatArtifactsList";
import type { ChatMessageView } from "../chatUtils";
import { primitiveHistory } from "../primitiveHistory";

/**
 * The Artifacts panel ("what has Baxter made in this conversation?") renders
 * the same `qa` parts the thread does, from the same messages. A card the
 * Living Map records as settled must therefore be read-only here too: the
 * operator meets one card in two places, and the two must not disagree.
 *
 * Measured on a snapshot of production: in the thread for the chat building
 * lg-inbox-organized-2738 the four answered cards render "Answered in the
 * thread" with the reply; in the dock's Artifacts tab on the same session the
 * same four render "Needs you" over live textareas and a Send button.
 */
function threadWithAnsweredQa(): ChatMessageView[] {
  return [
    {
      id: "m1",
      role: "assistant",
      content: "",
      parts: [
        {
          primitive: "qa",
          interactive: true,
          items: [
            { id: "shape", question: "Should hitting the cap offer a button you press?" },
          ],
        },
      ],
    },
    {
      id: "m2",
      role: "user",
      content: "A button you press. Never roll onto a successor on its own.",
    },
  ];
}

describe("Artifacts panel — a qa card answered in the thread", () => {
  it("renders read-only with the reply, not a Needs you card with a Send button", () => {
    const entries = primitiveHistory(threadWithAnsweredQa());
    render(
      <ChatArtifactsList entries={entries} onShowInChat={() => {}} onSubmit={() => {}} />,
    );

    // The chip agrees with the thread's copy of the same card.
    expect(screen.getByText("Answered in the thread")).toBeInTheDocument();
    expect(screen.queryByText(/Needs you/)).not.toBeInTheDocument();

    // A read-only card holds no control at all.
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Send/ })).not.toBeInTheDocument();

    // And it shows what the operator actually said.
    expect(
      screen.getByText("A button you press. Never roll onto a successor on its own."),
    ).toBeInTheDocument();
  });
});
