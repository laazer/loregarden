import type { TodoListPart } from "../../components/chat/primitives/types";
import { chunkStreamingReply } from "../streamingReply";

const PLAN: TodoListPart = {
  primitive: "todo_list",
  owner: "agent",
  plan_id: "land-1216",
  title: "Agent execution plan",
  items: [{ id: "baseline", text: "Record the baseline", checked: false }],
} as TodoListPart;

const PLAN_FENCE = [
  "```loregarden",
  JSON.stringify({ primitive: "todo_list", owner: "agent", plan_id: "land-1216", items: [] }),
  "```",
].join("\n");

describe("chunkStreamingReply", () => {
  it("keeps a paragraph still being written as the plain-text tail", () => {
    expect(chunkStreamingReply("I reproduced the **land", [])).toEqual([
      { kind: "tail", text: "I reproduced the **land" },
    ]);
  });

  it("settles paragraphs a blank line has finished", () => {
    expect(chunkStreamingReply("Done **one**.\n\nStarting", [])).toEqual([
      { kind: "markdown", text: "Done **one**." },
      { kind: "tail", text: "Starting" },
    ]);
  });

  it("turns a closed card fence into the validated plan, never raw JSON", () => {
    const chunks = chunkStreamingReply(`Diagnosis:\n\n${PLAN_FENCE}\n\nStarting with`, [PLAN]);
    expect(chunks).toEqual([
      { kind: "markdown", text: "Diagnosis:" },
      { kind: "card", primitive: "todo_list", plan: PLAN },
      { kind: "tail", text: "Starting with" },
    ]);
  });

  it("still matches the plan when the agent's JSON drops its closing brace", () => {
    // Seen live: the server repairs this and sends the plan; the stream must
    // show that card, not the "shown when the reply finishes" note.
    const fence = '```loregarden\n{"primitive":"todo_list","owner":"agent","plan_id":"land-1216","items":[]\n```';
    expect(chunkStreamingReply(fence, [PLAN])).toEqual([
      { kind: "card", primitive: "todo_list", plan: PLAN },
    ]);
  });

  it("names a card fence still being written instead of showing its JSON", () => {
    const chunks = chunkStreamingReply('Fixes:\n```loregarden\n{"primitive":"todo_list","items":[{"id', []);
    expect(chunks).toEqual([
      { kind: "markdown", text: "Fixes:" },
      { kind: "card-forming", primitive: "todo_list" },
    ]);
  });

  it("holds a closed card with no validated copy as a card, not text", () => {
    const chunks = chunkStreamingReply('```loregarden\n{"primitive":"qa","question":"x"}\n```', []);
    expect(chunks).toEqual([{ kind: "card", primitive: "qa", plan: null }]);
  });

  it("keeps an unclosed code fence in the tail and a closed one in markdown", () => {
    expect(chunkStreamingReply("Run:\n```bash\nnpm te", [])).toEqual([
      { kind: "markdown", text: "Run:" },
      { kind: "tail", text: "```bash\nnpm te" },
    ]);
    expect(chunkStreamingReply("```bash\nnpm test\n```\nThen", [])).toEqual([
      { kind: "markdown", text: "```bash\nnpm test\n```" },
      { kind: "tail", text: "Then" },
    ]);
  });
});
