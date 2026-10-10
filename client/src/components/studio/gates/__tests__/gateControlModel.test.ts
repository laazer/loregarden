import {
  collectWorkflowControls,
  collectWorkspaceControls,
  controlId,
  findGateControl,
} from "../gateControlModel";
import { agent, profile, stage, workflow, workspace } from "./gateStudioFixtures";

describe("workspace-wide controls", () => {
  it("lists each transition command and the script, stored in the profile file", () => {
    const controls = collectWorkspaceControls(
      workspace(),
      profile({ gates_commands: ["a", "b"], gates_transition_script: "ci/gate.py" }),
    );

    expect(controls.map((c) => c.id)).toEqual(["transition-command:0", "transition-command:1", "transition-script"]);
    expect(controls.every((c) => c.kind === "workspace_transition_command")).toBe(true);
    expect(controls[0].storedIn).toContain("agent_context/orchestration/blobert.yaml");
  });

  it("says no file stores them yet when the profile is the built-in fallback", () => {
    const [control] = collectWorkspaceControls(workspace(), profile({ source_path: null }));
    expect(control.storedIn).toMatch(/no orchestration profile file/i);
  });

  it("is empty without a profile or a workspace", () => {
    expect(collectWorkspaceControls(workspace(), null)).toEqual([]);
    expect(collectWorkspaceControls(null, profile())).toEqual([]);
  });
});

describe("workflow controls", () => {
  const input = (wf = workflow(), agents = [agent()]) => ({
    workspace: workspace(),
    profile: profile(),
    workflow: wf,
    agents,
  });

  it("gives a gate stage that also has exit actions a gate control plus one per action", () => {
    const gate = stage({
      key: "gate",
      name: "Gate",
      stage_type: "gate",
      exit_actions_enabled: true,
      exit_actions: [
        { key: "sign-off", label: "Sign off", requirement: { kind: "operator_judgment", decision_prompt: "Ship it?" } },
        { key: "push", label: "Push", requirement: { kind: "authority", authority_scope: "repo:push" } },
      ],
    });

    const controls = collectWorkflowControls(input(workflow({ stages: [gate] })));

    expect(controls.map((c) => c.kind)).toEqual(["workflow_gate_stage", "stage_exit_action", "stage_exit_action"]);
    expect(controls[1].value).toBe("Operator judgement — Ship it?");
    expect(controls[2].value).toBe("Authority — repo:push");
  });

  it("ignores exit actions on a stage that has them switched off", () => {
    const off = stage({
      exit_actions_enabled: false,
      exit_actions: [{ key: "x", label: "X", requirement: { kind: "credential", credential_key: "github_token" } }],
    });
    expect(collectWorkflowControls(input(workflow({ stages: [off] })))).toEqual([]);
  });

  it("finds agents through classify routes and parallel members, not only the stage agent", () => {
    const routed = stage({
      agent_id: "",
      stage_type: "classify",
      classify_routes: [{ agent_id: "router_target", skill_name: "", when: "", default: true }],
      parallel_agents: [{ agent_id: "reviewer", skill_name: "" }],
    } as never);
    const agents = [
      agent({ slug: "router_target", handoff_checks: [{ kind: "k", prompt: "Did you test it?" }] }),
      agent({ slug: "reviewer", gate_checks: [{ kind: "g", title: "No TODOs", impact: "blocks" }] }),
      agent({ slug: "unused", handoff_checks: [{ kind: "k", prompt: "never shown" }] }),
    ];

    const controls = collectWorkflowControls(input(workflow({ stages: [routed] }), agents));

    expect(controls.map((c) => c.title).sort()).toEqual(["Did you test it?", "No TODOs"]);
    expect(controls.every((c) => c.kind === "agent_handoff_check")).toBe(true);
  });
});

describe("control ids", () => {
  it("round-trip a stage key containing ':' through the URL", () => {
    const tricky = stage({ key: "a:b", name: "Tricky", stage_type: "gate" });
    const controls = collectWorkflowControls({
      workspace: workspace(),
      profile: profile(),
      workflow: workflow({ stages: [tricky] }),
      agents: [],
    });
    const id = controlId(["gate-stage", "a:b"]);

    expect(findGateControl(controls, id)?.title).toBe("Tricky");
  });

  it("answers a malformed or unknown id with a miss, never another control", () => {
    const controls = collectWorkspaceControls(workspace(), profile());
    expect(findGateControl(controls, "transition-command:%")).toBeNull();
    expect(findGateControl(controls, "transition-command::")).toBeNull();
    expect(findGateControl(controls, "transition-command:9")).toBeNull();
  });
});
