import { blockKindLabel, blockKindMeaning } from "../blockKinds";
import type { BlockKind } from "../../api/types";

const KINDS: BlockKind[] = ["harness", "work", "decision", "human_action"];

describe("block kinds", () => {
  it("tells the person whether they need to act, for every kind", () => {
    for (const kind of KINDS) {
      expect(blockKindLabel(kind)).not.toBe("");
      expect(blockKindMeaning(kind)).not.toBe("");
    }
    // The two a person need not touch say so; the two they must, say what.
    expect(blockKindMeaning("harness")).toMatch(/No action needed/);
    expect(blockKindMeaning("work")).toMatch(/No action needed/);
    expect(blockKindMeaning("decision")).toMatch(/Answer the question/);
    expect(blockKindMeaning("human_action")).toMatch(/your hands/);
  });
});
