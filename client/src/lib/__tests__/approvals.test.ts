/**
 * AC-1 / AC-2 (lg-inbox-organized-2738): the pure grouping behind the inbox
 * drawer's per-workspace headings.
 *
 * No React harness on purpose. `client/src/lib/approvals.ts` is the module that
 * holds pure derivations over `Approval[]`, and the ordering rule the drawer
 * depends on — gates first, then groups in the order their first card appears —
 * is decidable from an array alone. A component test that had to render cards
 * to find out which workspace leads would be checking the renderer, not the rule.
 */

import type { Approval } from "../../api/client";
import {
  type ApprovalGroup,
  groupApprovalsByWorkspace,
  inWorkspaceScope,
} from "../approvals";

/**
 * A pending approval. `cli_permission` with no checklist is deliberately NOT a
 * `hasHumanCriteria` kind, so `gate: true` is the only thing that promotes a
 * fixture — the sort is never accidentally satisfied by the default shape.
 */
function approval(
  id: string,
  workspace_slug: string,
  { gate = false }: { gate?: boolean } = {},
): Approval {
  return {
    id,
    title: id,
    level: "medium",
    workspace_slug,
    stage_key: "implement",
    stage_name: "Implement",
    impact: "",
    checklist: [],
    route_options: [],
    ticket_id: "ticket_1",
    ticket_external_id: "lg-1",
    kind: gate ? "workflow_gate" : "cli_permission",
    status: "pending",
    run_id: "",
    tool_name: "Bash",
    tool_input_json: "{}",
    cli_adapter: "",
  };
}

/** Group order, as slugs — the assertion most of these tests are about. */
const slugs = (groups: ApprovalGroup[]) => groups.map((group) => group.workspaceSlug);

/** Card order inside one named group. */
function idsIn(groups: ApprovalGroup[], workspaceSlug: string): string[] {
  const group = groups.find((candidate) => candidate.workspaceSlug === workspaceSlug);
  expect(group).toBeDefined();
  return (group as ApprovalGroup).approvals.map((item) => item.id);
}

describe("inWorkspaceScope", () => {
  it('admits every approval when the scope is "", whatever workspace it belongs to', () => {
    expect(inWorkspaceScope(approval("a", "blobert"), "")).toBe(true);
    expect(inWorkspaceScope(approval("b", "loregarden"), "")).toBe(true);
  });

  it("admits only an exact workspace match when the scope names one", () => {
    expect(inWorkspaceScope(approval("a", "blobert"), "blobert")).toBe(true);
    expect(inWorkspaceScope(approval("a", "blobert"), "loregarden")).toBe(false);
  });

  it("does not treat a prefix or a case variant as the same workspace", () => {
    expect(inWorkspaceScope(approval("a", "blobert"), "blob")).toBe(false);
    expect(inWorkspaceScope(approval("a", "blobert"), "Blobert")).toBe(false);
  });
});

describe("groupApprovalsByWorkspace", () => {
  // AC-2(a)
  it("keeps gates above permission prompts inside a group, API order within each rank", () => {
    const groups = groupApprovalsByWorkspace(
      [
        approval("perm-1", "blobert"),
        approval("perm-2", "blobert"),
        approval("gate-1", "blobert", { gate: true }),
        approval("perm-3", "blobert"),
        approval("gate-2", "blobert", { gate: true }),
      ],
      "",
    );

    expect(slugs(groups)).toEqual(["blobert"]);
    expect(idsIn(groups, "blobert")).toEqual([
      "gate-1",
      "gate-2",
      "perm-1",
      "perm-2",
      "perm-3",
    ]);
  });

  // AC-2(b)
  it("leads with the group holding a gate even when the other workspace came first from the API", () => {
    const groups = groupApprovalsByWorkspace(
      [
        approval("perm-1", "loregarden"),
        approval("perm-2", "loregarden"),
        approval("gate-1", "blobert", { gate: true }),
      ],
      "",
    );

    expect(slugs(groups)).toEqual(["blobert", "loregarden"]);
    expect(idsIn(groups, "blobert")).toEqual(["gate-1"]);
    expect(idsIn(groups, "loregarden")).toEqual(["perm-1", "perm-2"]);
  });

  // AC-2(c)
  it("falls back to first appearance in the sorted array when both groups hold a gate", () => {
    // API order puts loregarden first, but blobert's gate outranks loregarden's
    // permission prompt, so blobert's gate is the first card of the sorted list.
    const groups = groupApprovalsByWorkspace(
      [
        approval("lore-perm", "loregarden"),
        approval("blob-gate", "blobert", { gate: true }),
        approval("lore-gate", "loregarden", { gate: true }),
        approval("blob-perm", "blobert"),
      ],
      "",
    );

    expect(slugs(groups)).toEqual(["blobert", "loregarden"]);
    expect(idsIn(groups, "blobert")).toEqual(["blob-gate", "blob-perm"]);
    expect(idsIn(groups, "loregarden")).toEqual(["lore-gate", "lore-perm"]);
  });

  it("orders two gate-led groups by which gate the API returned first", () => {
    const groups = groupApprovalsByWorkspace(
      [
        approval("lore-gate", "loregarden", { gate: true }),
        approval("blob-gate", "blobert", { gate: true }),
      ],
      "",
    );

    expect(slugs(groups)).toEqual(["loregarden", "blobert"]);
  });

  // AC-2(d)
  it("drops other workspaces' approvals when the scope names one", () => {
    const groups = groupApprovalsByWorkspace(
      [
        approval("blob-1", "blobert"),
        approval("lore-1", "loregarden", { gate: true }),
        approval("blob-2", "blobert"),
      ],
      "blobert",
    );

    expect(slugs(groups)).toEqual(["blobert"]);
    expect(idsIn(groups, "blobert")).toEqual(["blob-1", "blob-2"]);
  });

  // AC-2(e)
  it("emits no group with zero approvals", () => {
    const groups = groupApprovalsByWorkspace(
      [
        approval("blob-1", "blobert"),
        approval("lore-1", "loregarden", { gate: true }),
        approval("third-1", "tinkercg"),
      ],
      "",
    );

    expect(groups).toHaveLength(3);
    for (const group of groups) {
      expect(group.approvals.length).toBeGreaterThan(0);
    }
  });

  it("emits no group for a workspace whose only approvals were filtered out", () => {
    const groups = groupApprovalsByWorkspace(
      [approval("blob-1", "blobert"), approval("lore-1", "loregarden")],
      "loregarden",
    );

    expect(slugs(groups)).toEqual(["loregarden"]);
  });

  // AC-2(f)
  it("returns [] for an empty input", () => {
    expect(groupApprovalsByWorkspace([], "")).toEqual([]);
    expect(groupApprovalsByWorkspace([], "blobert")).toEqual([]);
  });

  it("returns [] when the scope matches nothing", () => {
    expect(
      groupApprovalsByWorkspace([approval("blob-1", "blobert")], "loregarden"),
    ).toEqual([]);
  });

  // AC-2(g)
  it("yields exactly one group when every approval is in one workspace", () => {
    const groups = groupApprovalsByWorkspace(
      [
        approval("a", "blobert"),
        approval("b", "blobert", { gate: true }),
        approval("c", "blobert"),
      ],
      "",
    );

    expect(groups).toHaveLength(1);
    expect(groups[0].workspaceSlug).toBe("blobert");
    expect(groups[0].approvals).toHaveLength(3);
  });

  // AC-2(h)
  it("does not mutate or reorder the caller's array", () => {
    // The caller here is the component's `approvals.data` — react-query's own
    // cached array. Sorting it in place would reorder the cache every poll and
    // make a second observer's render depend on which one rendered first.
    const input = [
      approval("perm-1", "loregarden"),
      approval("gate-1", "blobert", { gate: true }),
      approval("perm-2", "loregarden"),
    ];
    const before = input.map((item) => item.id);

    groupApprovalsByWorkspace(input, "");

    expect(input.map((item) => item.id)).toEqual(before);
    expect(input).toHaveLength(3);
  });

  it("returns approvals by reference, so a group is a view and not a copy", () => {
    const gate = approval("gate-1", "blobert", { gate: true });
    const groups = groupApprovalsByWorkspace([gate], "");

    expect(groups[0].approvals[0]).toBe(gate);
  });
});

/* ------------------------------------------------------------------ *
 * Adversarial coverage (test-break, lg-inbox-organized-2738).
 *
 * The cases above pin AC-2's eight clauses. These pin the places where an
 * implementation can satisfy all eight and still be wrong — the gate rank
 * reduced to one kind, a slug the data really carries, and the properties
 * AC-1 states as prose ("never mutates", "keyed by workspace_slug") rather
 * than as an ordering.
 * ------------------------------------------------------------------ */

/** An approval of any kind — `gate: true` above can only make a `workflow_gate`. */
function approvalOf(
  id: string,
  workspace_slug: string,
  overrides: Partial<Approval>,
): Approval {
  return { ...approval(id, workspace_slug), ...overrides };
}

describe("the gate rank is hasHumanCriteria, not one kind", () => {
  /**
   * AC-1 pins the comparator on `hasHumanCriteria`, whose true-set is four
   * things: `workflow_gate`, `rework_pause`, `stage_park`, and ANY kind
   * carrying a checklist (`utils/approvalCriteria.ts:11-21`). Every fixture
   * above is a `workflow_gate`, so an implementation that inlined
   * `kind === "workflow_gate"` passes all of AC-2 and still buries three of
   * the four — including `rework_pause`, which plan-synthesis had to correct
   * the risk lane about, and which the live 2026-11-18 distribution actually
   * contains. The bug would reach an operator as a paused rework loop filed
   * under a workspace that sorted last.
   */
  it.each(["workflow_gate", "rework_pause", "stage_park"] as const)(
    "ranks a %s above a permission prompt, and leads its group with it",
    (kind) => {
      const groups = groupApprovalsByWorkspace(
        [
          approval("lore-perm", "loregarden"),
          approval("blob-perm", "blobert"),
          approvalOf("blob-human", "blobert", { kind }),
        ],
        "",
      );

      expect(slugs(groups)).toEqual(["blobert", "loregarden"]);
      expect(idsIn(groups, "blobert")).toEqual(["blob-human", "blob-perm"]);
    },
  );

  it("ranks a checklist-carrying permission prompt as a gate", () => {
    // The server expands `{{acceptance_criteria}}` into checklist items, so a
    // checklist is the same "read this and decide" ask arriving on any kind.
    const groups = groupApprovalsByWorkspace(
      [
        approval("lore-perm", "loregarden"),
        approvalOf("blob-checklist", "blobert", {
          checklist: ["Play-test by hand — the drawer groups by workspace"],
        }),
      ],
      "",
    );

    expect(slugs(groups)).toEqual(["blobert", "loregarden"]);
  });

  it("does not rank an EMPTY checklist as a gate", () => {
    // `Boolean(approval.checklist?.length)` — the boundary between the two
    // ranks. A truthiness test on the array itself would promote every card.
    const groups = groupApprovalsByWorkspace(
      [
        approval("lore-perm", "loregarden"),
        approvalOf("blob-empty", "blobert", { checklist: [] }),
      ],
      "",
    );

    expect(slugs(groups)).toEqual(["loregarden", "blobert"]);
  });

  it.each(["block_decision", "human_action", "cli_question"] as const)(
    "leaves a %s in API order — it is not a gate-class kind",
    (kind) => {
      const groups = groupApprovalsByWorkspace(
        [
          approval("lore-perm", "loregarden"),
          approvalOf("blob-other", "blobert", { kind }),
        ],
        "",
      );

      expect(slugs(groups)).toEqual(["loregarden", "blobert"]);
    },
  );
});

describe("groupApprovalsByWorkspace — edges and properties", () => {
  it("groups an approval whose workspace_slug is empty rather than dropping it", () => {
    // The payload's slug is a string the server fills in; an approval that
    // arrived without one is still a BLOCKED RUN, and the drawer's whole
    // stated reason for `workspaceSlug === ""` is that no blocked run is
    // hidden. AC-1 keys the Map on `workspace_slug` with no special case, so
    // the empty slug is its own group and the card survives to be rendered.
    const groups = groupApprovalsByWorkspace(
      [approval("orphan", ""), approval("blob-gate", "blobert", { gate: true })],
      "",
    );

    expect(slugs(groups)).toEqual(["blobert", ""]);
    expect(idsIn(groups, "")).toEqual(["orphan"]);
  });

  it("keeps two approvals that share an id, in their own workspaces", () => {
    // De-duplication is `mergeApprovals`' job and the duplicate BADGE is
    // `duplicateApprovalIds`'. A grouping function that also deduped would
    // silently drop one of two real decisions.
    const groups = groupApprovalsByWorkspace(
      [approval("same-id", "blobert"), approval("same-id", "loregarden")],
      "",
    );

    expect(slugs(groups)).toEqual(["blobert", "loregarden"]);
    expect(idsIn(groups, "blobert")).toEqual(["same-id"]);
    expect(idsIn(groups, "loregarden")).toEqual(["same-id"]);
  });

  it("returns the same answer twice for the same input", () => {
    // Determinism over a sort whose comparator returns 0 for most pairs: an
    // unstable sort, or one that consumed the input, would differ on the
    // second call. The drawer runs this on every 5s poll.
    const input = [
      approval("lore-perm", "loregarden"),
      approval("blob-gate", "blobert", { gate: true }),
      approval("lore-gate", "loregarden", { gate: true }),
      approval("blob-perm", "blobert"),
    ];

    const first = groupApprovalsByWorkspace(input, "");
    const second = groupApprovalsByWorkspace(input, "");

    expect(slugs(second)).toEqual(slugs(first));
    expect(idsIn(second, "blobert")).toEqual(idsIn(first, "blobert"));
    expect(idsIn(second, "loregarden")).toEqual(idsIn(first, "loregarden"));
  });

  it("hands back arrays the caller can hold without affecting a later call", () => {
    // A group whose array IS the caller's array (or a shared module-level one)
    // turns a render-time `.push` or `.sort` into a corrupted next poll.
    const input = [approval("a", "blobert"), approval("b", "blobert")];

    const first = groupApprovalsByWorkspace(input, "");
    first[0].approvals.push(approval("injected", "blobert"));

    expect(groupApprovalsByWorkspace(input, "")[0].approvals.map((x) => x.id)).toEqual([
      "a",
      "b",
    ]);
    expect(input).toHaveLength(2);
  });

  it("holds its order at the all-time concurrent peak, 24 pending", () => {
    // Measured ceiling (ui-design, live DB 2026-11-18): 24 globally, 14 in one
    // workspace. Nothing is capped, dropped or re-ordered at volume, and the
    // interleaving is deliberate — every group's cards are non-contiguous in
    // the API response, which is the shape a Map walk over a SORTED array has
    // to survive.
    const WORKSPACES = ["blobert", "loregarden", "tinkercg"];
    const input = Array.from({ length: 24 }, (_, index) =>
      approval(`card-${index}`, WORKSPACES[index % 3], { gate: index === 7 }),
    );

    const groups = groupApprovalsByWorkspace(input, "");

    // index 7 is the only gate and belongs to loregarden (7 % 3 === 1).
    expect(slugs(groups)).toEqual(["loregarden", "blobert", "tinkercg"]);
    expect(groups.reduce((total, group) => total + group.approvals.length, 0)).toBe(24);
    expect(groups.map((group) => group.approvals.length)).toEqual([8, 8, 8]);
    expect(idsIn(groups, "loregarden")[0]).toBe("card-7");
  });

  it("scopes to one workspace at volume without emitting the others", () => {
    const input = Array.from({ length: 24 }, (_, index) =>
      approval(`card-${index}`, index % 2 === 0 ? "blobert" : "loregarden"),
    );

    const groups = groupApprovalsByWorkspace(input, "loregarden");

    expect(slugs(groups)).toEqual(["loregarden"]);
    expect(groups[0].approvals).toHaveLength(12);
  });
});

describe("inWorkspaceScope — no normalization", () => {
  it("does not trim, so a padded scope matches nothing", () => {
    // The slug is compared as stored. Trimming here would be defensive
    // normalization of a value the server already normalized, and it would
    // make a scope typo look like an empty workspace instead of a bad filter.
    expect(inWorkspaceScope(approval("a", "blobert"), " blobert")).toBe(false);
    expect(inWorkspaceScope(approval("a", " blobert"), "blobert")).toBe(false);
  });

  it("admits an approval with an empty slug only under the all-workspaces scope", () => {
    expect(inWorkspaceScope(approval("a", ""), "")).toBe(true);
    expect(inWorkspaceScope(approval("a", ""), "blobert")).toBe(false);
  });
});
