import { fireEvent, render, screen } from "@testing-library/react";

import { StudioChatMessages } from "../../../studio/StudioChat";
import { agentPlanRunSummary, supersededAgentPlanKeys } from "../agentPlan";
import { TodoListPrimitive } from "../TodoListPrimitive";
import type { ChatPart, TodoListPart } from "../types";

const plan = (planId: string | null, checked: boolean): TodoListPart => ({
  primitive: "todo_list",
  owner: "agent",
  plan_id: planId,
  title: "Agent execution plan",
  items: [{ id: "api", text: "Add history API", checked }],
});

describe("agent execution plan identity", () => {
  it("supersedes every plan card but the newest with the same identity", () => {
    const messages = [
      { id: "m1", role: "assistant", content: "", parts: [plan("history", false)] },
      { id: "m2", role: "assistant", content: "", parts: [plan("history", true)] },
      { id: "m3", role: "assistant", content: "", parts: [plan("other", false)] },
    ];
    expect([...supersededAgentPlanKeys(messages)]).toEqual(["m1#0"]);
  });

  it("falls back to the title when the agent omits a plan id", () => {
    const messages = [
      { id: "m1", role: "assistant", content: "", parts: [plan(null, false)] },
      { id: "m2", role: "assistant", content: "", parts: [plan(null, true)] },
    ];
    expect([...supersededAgentPlanKeys(messages)]).toEqual(["m1#0"]);
  });

  it("leaves user checklists alone", () => {
    const checklist: ChatPart = {
      primitive: "todo_list",
      owner: "user",
      title: "Your checklist",
      items: [{ id: "a", text: "Review diff", checked: false }],
    };
    const messages = [
      { id: "m1", role: "assistant", content: "", parts: [checklist] },
      { id: "m2", role: "assistant", content: "", parts: [checklist] },
    ];
    expect(supersededAgentPlanKeys(messages).size).toBe(0);
  });

  it("renders only the newest plan card in the thread", () => {
    render(
      <StudioChatMessages
        messages={[
          { id: "m1", role: "assistant", content: "", parts: [plan("history", false)] },
          { id: "m2", role: "assistant", content: "", parts: [plan("history", true)] },
        ]}
        showAssistantAvatar={false}
      />,
    );
    expect(screen.getAllByText("Agent execution plan")).toHaveLength(1);
    expect(screen.getByText("1/1 complete")).toBeInTheDocument();
  });

  it("asks for the same plan id back when the card carries one", () => {
    const onSubmit = jest.fn();
    render(<TodoListPrimitive part={plan("history-api", false)} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(onSubmit.mock.calls[0][0]).toContain('plan_id "history-api"');
  });
});

describe("agent execution plan Run message", () => {
  const runMessage = [
    "Execute this agent execution plan now. Complete each unchecked step using tools.",
    "",
    "Plan: Fix chat plans",
    "- [ ] Add history API (id: api)",
    "- [x] Run focused tests (id: verify)",
  ].join("\n");

  it("summarizes only the Run payload", () => {
    expect(agentPlanRunSummary("What should we ship today?")).toBeNull();
    expect(agentPlanRunSummary(runMessage)).toEqual({ title: "Fix chat plans", steps: 1 });
  });

  it("shows the Run turn as an action, not the operator quoting the plan back", () => {
    render(
      <StudioChatMessages
        messages={[{ id: "u1", role: "user", content: runMessage }]}
        showAssistantAvatar={false}
      />,
    );
    expect(screen.getByText("Ran Fix chat plans · 1 step")).toBeInTheDocument();
    expect(screen.queryByText(/Add history API/)).toBeNull();
  });
});
