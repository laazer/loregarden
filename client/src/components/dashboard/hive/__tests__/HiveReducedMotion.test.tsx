import { act, render } from "@testing-library/react";

import type { WorkflowStageView } from "../../../../api/client";
import { tilePercent } from "../../../../lib/hive/coords";
import { buildHiveWorld, type HiveAgentState } from "../../../../lib/hive/worldModel";
import { HiveCssFloor } from "../HiveCssFloor";

const REDUCE = "(prefers-reduced-motion: reduce)";

/** A matchMedia whose reduced-motion answer the test can flip, firing `change`. */
function mockReducedMotion(initial: boolean) {
  let reduced = initial;
  const listeners = new Set<() => void>();
  window.matchMedia = jest.fn((query: string) => ({
    get matches() {
      return query === REDUCE && reduced;
    },
    media: query,
    onchange: null,
    addEventListener: (_: string, fn: () => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
    addListener: jest.fn(),
    removeListener: jest.fn(),
    dispatchEvent: jest.fn(),
  })) as unknown as typeof window.matchMedia;
  return (next: boolean) => {
    reduced = next;
    act(() => listeners.forEach((fn) => fn()));
  };
}

const model = () =>
  buildHiveWorld(
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

/** An agent whose job takes it away from its desk, so a walk is visible. */
function travellingAgent(world: ReturnType<typeof model>): HiveAgentState {
  const agent = world.agents.find((a) => a.desk.x !== a.target.x || a.desk.y !== a.target.y);
  if (!agent) throw new Error("fixture has no agent whose target differs from its desk");
  return agent;
}

function agentElement(container: HTMLElement, agent: HiveAgentState): HTMLElement {
  const el = [...container.querySelectorAll<HTMLElement>(".hive-css__agent")].find((node) =>
    node.title.startsWith(`${agent.name} ·`),
  );
  if (!el) throw new Error(`no element for ${agent.name}`);
  return el;
}

beforeEach(() => jest.useFakeTimers());
afterEach(() => jest.useRealTimers());

it("walks an agent toward its target when motion is allowed", () => {
  mockReducedMotion(false);
  const world = model();
  const agent = travellingAgent(world);
  const { container } = render(<HiveCssFloor model={world} />);
  const el = agentElement(container, agent);
  const start = { left: el.style.left, top: el.style.top };

  act(() => {
    jest.advanceTimersByTime(1500);
  });

  expect({ left: el.style.left, top: el.style.top }).not.toEqual(start);
});

it("stands an agent at its target, without walking, when reduced motion is on", () => {
  mockReducedMotion(true);
  const world = model();
  const agent = travellingAgent(world);
  const target = tilePercent(agent.target, world.layout.map);
  const { container } = render(<HiveCssFloor model={world} />);
  const el = agentElement(container, agent);

  expect({ left: el.style.left, top: el.style.top }).toEqual(target);
  act(() => {
    jest.advanceTimersByTime(5000);
  });
  expect({ left: el.style.left, top: el.style.top }).toEqual(target);
  expect(el).not.toHaveClass("hive-css__agent--walking");
  // What the agent is doing stays readable when it no longer moves.
  expect(el.title).toContain(agent.statusLabel);
});

it("applies a change of the OS setting without a reload", () => {
  const setReduced = mockReducedMotion(false);
  const world = model();
  const agent = travellingAgent(world);
  const target = tilePercent(agent.target, world.layout.map);
  const { container } = render(<HiveCssFloor model={world} />);
  const el = agentElement(container, agent);

  setReduced(true);

  expect({ left: el.style.left, top: el.style.top }).toEqual(target);
});
