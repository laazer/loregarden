import { primitiveHistory, primitiveLabel } from "../primitiveHistory";
import type { ChatMessageView } from "../chatUtils";

function turn(id: string, role: string, parts: ChatMessageView["parts"]): ChatMessageView {
  return { id, role, content: "", parts };
}

describe("primitiveHistory", () => {
  it("lists assistant cards newest first, skipping prose, reasoning and user turns", () => {
    const history = primitiveHistory([
      turn("a1", "assistant", [
        { primitive: "text", content: "Here" },
        { primitive: "ticket", ticket_id: "T-1" },
      ]),
      turn("u1", "user", [{ primitive: "ticket", ticket_id: "T-9" }]),
      turn("a2", "assistant", [
        { primitive: "thinking", content: "hmm" },
        { primitive: "kanban", title: "Delivery board" },
      ]),
    ]);

    expect(history.map((entry) => entry.key)).toEqual(["a2:1", "a1:1"]);
    expect(history.map((entry) => entry.messageId)).toEqual(["a2", "a1"]);
  });

  it("drops a plan card that a later copy of the same plan replaced", () => {
    const plan = (checked: boolean) => ({
      primitive: "todo_list" as const,
      owner: "agent",
      plan_id: "p1",
      items: [{ id: "x", text: "Do it", checked }],
    });
    const history = primitiveHistory([
      turn("a1", "assistant", [plan(false)]),
      turn("a2", "assistant", [plan(true)]),
    ]);

    expect(history.map((entry) => entry.key)).toEqual(["a2:0"]);
  });

  it("names a card by its most specific field", () => {
    expect(primitiveLabel({ primitive: "kanban", title: "Board A" })).toBe("Board A");
    expect(primitiveLabel({ primitive: "ticket", ticket_id: "T-1" })).toBe("T-1");
    expect(primitiveLabel({ primitive: "calendar" })).toBe("Calendar");
  });
});
