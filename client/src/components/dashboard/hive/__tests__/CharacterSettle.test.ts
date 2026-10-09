import type { WorkflowStageView } from "../../../../api/client";
import { buildHiveWorld } from "../../../../lib/hive/worldModel";
import type { HiveSkinTextures } from "../scene/assets";
import { CharacterView } from "../scene/Character";
import { tileToWorld } from "../scene/pathfinding";

// pixi is mocked; the view only needs something to hand its sprites.
const textures = { agent: { worker: {} } } as unknown as HiveSkinTextures;

const world = buildHiveWorld(
  [
    {
      key: "impl",
      name: "Implementation",
      agent_id: "backend_implementer",
      status: "running",
      skill_name: "apply_patch",
      optional: false,
      note: "",
      stage_type: "agent",
      agents: [],
    } satisfies WorkflowStageView,
  ],
  { skin: "officeplace" },
);
const agent = world.agents.find((a) => a.desk.x !== a.target.x || a.desk.y !== a.target.y)!;

it("settles a character at the end of its path in one step, and it stays there", () => {
  const view = new CharacterView(agent, textures, world.layout.walkGrid);
  const desk = tileToWorld(agent.desk);
  expect({ x: view.x, y: view.y }).toEqual({ x: Math.round(desk.x), y: Math.round(desk.y) });

  view.settle();
  const target = tileToWorld(agent.target);
  const settled = { x: Math.round(target.x), y: Math.round(target.y) };
  expect({ x: view.x, y: view.y }).toEqual(settled);

  view.update(1, true);
  expect({ x: view.x, y: view.y }).toEqual(settled);
});
