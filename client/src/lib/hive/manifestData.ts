import type { HiveSkinId } from "./skins";

export type HiveManifestJson = {
  skin: string;
  floor: string;
  agents: Record<string, string>;
  stations: Record<string, string>;
  artifacts: Record<string, string>;
  events: Record<string, string>;
};

export const HIVE_MANIFEST_DATA: Record<HiveSkinId, HiveManifestJson> = {
  warcraft: {
    skin: "warcraft",
    floor: "warcraft/floor.png",
    agents: {
      worker: "warcraft/agents/worker.png",
      planner: "warcraft/agents/planner.png",
      implementer: "warcraft/agents/implementer.png",
      tester: "warcraft/agents/tester.png",
      reviewer: "warcraft/agents/reviewer.png",
    },
    stations: {
      planner_hq: "warcraft/stations/planner_hq.png",
      research: "warcraft/stations/research.png",
      coding: "warcraft/stations/coding.png",
      testing: "warcraft/stations/testing.png",
      deploy: "warcraft/stations/deploy.png",
    },
    artifacts: {
      context: "warcraft/artifacts/context.png",
      diff: "warcraft/artifacts/diff.png",
    },
    events: {
      waiting: "warcraft/events/waiting.png",
      error: "warcraft/events/error.png",
    },
  },
  dunder_mifflin: {
    skin: "dunder_mifflin",
    floor: "dunder_mifflin/floor.png",
    agents: {
      worker: "dunder_mifflin/agents/worker.png",
      planner: "dunder_mifflin/agents/planner.png",
      implementer: "dunder_mifflin/agents/implementer.png",
      tester: "dunder_mifflin/agents/tester.png",
      reviewer: "dunder_mifflin/agents/reviewer.png",
    },
    stations: {
      planner_hq: "dunder_mifflin/stations/planner_hq.png",
      research: "dunder_mifflin/stations/research.png",
      coding: "dunder_mifflin/stations/coding.png",
      testing: "dunder_mifflin/stations/testing.png",
      deploy: "dunder_mifflin/stations/deploy.png",
    },
    artifacts: {
      context: "dunder_mifflin/artifacts/context.png",
      diff: "dunder_mifflin/artifacts/diff.png",
    },
    events: {
      waiting: "dunder_mifflin/events/waiting.png",
      error: "dunder_mifflin/events/error.png",
    },
  },
  cyberpunk: {
    skin: "cyberpunk",
    floor: "cyberpunk/floor.png",
    agents: {
      worker: "cyberpunk/agents/worker.png",
      planner: "cyberpunk/agents/planner.png",
      implementer: "cyberpunk/agents/implementer.png",
      tester: "cyberpunk/agents/tester.png",
      reviewer: "cyberpunk/agents/reviewer.png",
    },
    stations: {
      planner_hq: "cyberpunk/stations/planner_hq.png",
      research: "cyberpunk/stations/research.png",
      coding: "cyberpunk/stations/coding.png",
      testing: "cyberpunk/stations/testing.png",
      deploy: "cyberpunk/stations/deploy.png",
    },
    artifacts: {
      context: "cyberpunk/artifacts/context.png",
      diff: "cyberpunk/artifacts/diff.png",
    },
    events: {
      waiting: "cyberpunk/events/waiting.png",
      error: "cyberpunk/events/error.png",
    },
  },
  starcraft: {
    skin: "starcraft",
    floor: "starcraft/floor.png",
    agents: {
      worker: "starcraft/agents/worker.png",
      planner: "starcraft/agents/planner.png",
      implementer: "starcraft/agents/implementer.png",
      tester: "starcraft/agents/tester.png",
      reviewer: "starcraft/agents/reviewer.png",
    },
    stations: {
      planner_hq: "starcraft/stations/planner_hq.png",
      research: "starcraft/stations/research.png",
      coding: "starcraft/stations/coding.png",
      testing: "starcraft/stations/testing.png",
      deploy: "starcraft/stations/deploy.png",
    },
    artifacts: {
      context: "starcraft/artifacts/context.png",
      diff: "starcraft/artifacts/diff.png",
    },
    events: {
      waiting: "starcraft/events/waiting.png",
      error: "starcraft/events/error.png",
    },
  },
};
