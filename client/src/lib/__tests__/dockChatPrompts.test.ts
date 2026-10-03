import {
  CONFIRM_PROMPT,
  DECLINE_PROMPT,
  ELABORATE_PROMPT,
  SHIP_PROMPT,
  followUpPrompts,
  replyPrompts,
} from "../dockChatPrompts";
import type { ChatMessageView } from "../../components/chat/chatUtils";

function user(content: string, id = "u1"): ChatMessageView {
  return { id, role: "user", content };
}

function assistant(content: string, parts: ChatMessageView["parts"] = [], id = "a1"): ChatMessageView {
  return { id, role: "assistant", content, parts };
}

describe("followUpPrompts", () => {
  it("offers the openers on an empty thread", () => {
    expect(followUpPrompts("ticket-triage", null, [])).toEqual([
      "What is blocking this ticket?",
      "Summarise what the last run changed",
      "Why did the last stage fail?",
    ]);
  });

  it("leads with yes and no when the reply ended on a question", () => {
    const prompts = followUpPrompts("baxter-home", null, [
      user("Clean up the stale branches"),
      assistant("I found 4 merged branches. Should I delete them?"),
    ]);
    expect(prompts.slice(0, 2)).toEqual([CONFIRM_PROMPT, DECLINE_PROMPT]);
  });

  it("ignores a question mark inside a code block", () => {
    const prompts = replyPrompts([
      assistant("Here is the regex:\n```\n^a?b$\n```\nIt matches both forms."),
    ]);
    expect(prompts).not.toContain(CONFIRM_PROMPT);
  });

  it("answers the cards the reply drew", () => {
    const prompts = replyPrompts([
      assistant("Proposed change:", [{ primitive: "edit", content: "x" }]),
    ]);
    expect(prompts).toEqual(["Apply this change"]);
  });

  it("offers a fix when the reply reports a failure", () => {
    expect(replyPrompts([assistant("The test stage failed on pytest.")])).toContain(
      "How do we fix it?",
    );
  });

  it("reads the latest reply, not an earlier one", () => {
    const prompts = replyPrompts([
      assistant("Shall I go on?", [], "a1"),
      user("yes"),
      assistant("Done — all green.", [], "a2"),
    ]);
    expect(prompts).toEqual([]);
  });

  it("never offers back a prompt the operator already sent", () => {
    const prompts = followUpPrompts("ticket-triage", null, [
      user("What is blocking this ticket?"),
      assistant("Nothing is blocking it."),
    ]);
    expect(prompts).not.toContain("What is blocking this ticket?");
    expect(prompts).toContain(ELABORATE_PROMPT);
  });

  it("keeps shipping on a branch of its own, after the reply's own prompts", () => {
    const prompts = followUpPrompts("branch-triage", "feat/x", [
      user("Is it ready?"),
      assistant("Tests pass. Want me to open the PR?"),
    ]);
    expect(prompts.indexOf(SHIP_PROMPT)).toBeGreaterThan(prompts.indexOf(DECLINE_PROMPT));
  });
});
