import type { HiveSkinId } from "./skins";

export type HiveManifestJson = {
  skin: string;
  floor: string;
  scenery?: string;
  agents: Record<string, string>;
  stations: Record<string, string>;
  artifacts: Record<string, string>;
  events: Record<string, string>;
};

export const HIVE_MANIFEST_DATA: Record<HiveSkinId, HiveManifestJson> = {
  runeplace: {
    skin: "runeplace",
    floor: "runeplace/floor.png",
    agents: {
      worker: "runeplace/agents/worker.png",
      planner: "runeplace/agents/planner.png",
      implementer: "runeplace/agents/implementer.png",
      tester: "runeplace/agents/tester.png",
      reviewer: "runeplace/agents/reviewer.png",
    },
    stations: {
      planner_hq: "runeplace/stations/planner_hq.png",
      research: "runeplace/stations/research.png",
      coding: "runeplace/stations/coding.png",
      testing: "runeplace/stations/testing.png",
      deploy: "runeplace/stations/deploy.png",
    },
    artifacts: {
      context: "runeplace/artifacts/context.png",
      diff: "runeplace/artifacts/diff.png",
    },
    events: {
      waiting: "runeplace/events/waiting.png",
      error: "runeplace/events/error.png",
    },
  },
  officeplace: {
    skin: "officeplace",
    floor: "officeplace/floor.png",
    scenery: "officeplace/scenery.png",
    agents: {
      worker: "officeplace/agents/worker.png",
      planner: "officeplace/agents/planner.png",
      implementer: "officeplace/agents/implementer.png",
      tester: "officeplace/agents/tester.png",
      reviewer: "officeplace/agents/reviewer.png",
    },
    stations: {
      planner_hq: "officeplace/stations/planner_hq.png",
      research: "officeplace/stations/research.png",
      coding: "officeplace/stations/coding.png",
      testing: "officeplace/stations/testing.png",
      deploy: "officeplace/stations/deploy.png",
    },
    artifacts: {
      context: "officeplace/artifacts/context.png",
      diff: "officeplace/artifacts/diff.png",
    },
    events: {
      waiting: "officeplace/events/waiting.png",
      error: "officeplace/events/error.png",
    },
  },
  netplace: {
    skin: "netplace",
    floor: "netplace/floor.png",
    agents: {
      worker: "netplace/agents/worker.png",
      planner: "netplace/agents/planner.png",
      implementer: "netplace/agents/implementer.png",
      tester: "netplace/agents/tester.png",
      reviewer: "netplace/agents/reviewer.png",
    },
    stations: {
      planner_hq: "netplace/stations/planner_hq.png",
      research: "netplace/stations/research.png",
      coding: "netplace/stations/coding.png",
      testing: "netplace/stations/testing.png",
      deploy: "netplace/stations/deploy.png",
    },
    artifacts: {
      context: "netplace/artifacts/context.png",
      diff: "netplace/artifacts/diff.png",
    },
    events: {
      waiting: "netplace/events/waiting.png",
      error: "netplace/events/error.png",
    },
  },
  starplace: {
    skin: "starplace",
    floor: "starplace/floor.png",
    agents: {
      worker: "starplace/agents/worker.png",
      planner: "starplace/agents/planner.png",
      implementer: "starplace/agents/implementer.png",
      tester: "starplace/agents/tester.png",
      reviewer: "starplace/agents/reviewer.png",
    },
    stations: {
      planner_hq: "starplace/stations/planner_hq.png",
      research: "starplace/stations/research.png",
      coding: "starplace/stations/coding.png",
      testing: "starplace/stations/testing.png",
      deploy: "starplace/stations/deploy.png",
    },
    artifacts: {
      context: "starplace/artifacts/context.png",
      diff: "starplace/artifacts/diff.png",
    },
    events: {
      waiting: "starplace/events/waiting.png",
      error: "starplace/events/error.png",
    },
  },
};
