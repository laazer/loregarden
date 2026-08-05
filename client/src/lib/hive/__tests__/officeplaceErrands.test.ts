import { findPathTiles } from "../pathfinding";
import { createOfficeplaceOpenWalkGrid } from "../layouts/walkGrid";
import {
  OFFICEPLACE_ERRANDS,
  OFFICEPLACE_MAP,
  OFFICEPLACE_RECEPTIONIST,
  type HiveOfficeErrand,
} from "../layouts/officeplaceLayout";

const EXPECTED_ERRANDS: HiveOfficeErrand[] = [
  {
    id: "lobby-elevator",
    label: "Waiting for the lobby elevator",
    object: { id: "lobby-elevator-door", label: "Lobby elevator", tile: { x: 28, y: 5 } },
    stand: { x: 28, y: 6 },
  },
  {
    id: "kitchen-coffee",
    label: "Getting coffee in the kitchen",
    object: { id: "kitchen-counter", label: "Kitchen counter", tile: { x: 44, y: 5 } },
    stand: { x: 44, y: 6 },
  },
  {
    id: "break-room-table",
    label: "Tidying the break-room table",
    object: {
      id: "break-room-lower-table",
      label: "Break-room table",
      tile: { x: 4, y: 43 },
    },
    stand: { x: 4, y: 42 },
  },
  {
    id: "plant-watering",
    label: "Watering the lobby plant",
    object: { id: "lobby-west-plant", label: "Lobby plant", tile: { x: 1, y: 6 } },
    stand: { x: 2, y: 6 },
  },
  {
    id: "conference-exit",
    label: "Leaving the conference room",
    object: {
      id: "conference-east-exit",
      label: "Conference-room exit",
      tile: { x: 34, y: 39 },
    },
    stand: { x: 33, y: 40 },
  },
  {
    id: "annex-elevator",
    label: "Waiting for the annex elevator",
    object: { id: "annex-elevator-door", label: "Annex elevator", tile: { x: 55, y: 40 } },
    stand: { x: 54, y: 40 },
  },
  {
    id: "reception-counter",
    label: "Checking in at reception",
    object: { id: "reception-counter", label: "Reception counter", tile: { x: 43, y: 27 } },
    stand: { x: 42, y: 28 },
  },
  {
    id: "stairwell-check",
    label: "Checking the stairwell",
    object: {
      id: "stairwell-lower-landing",
      label: "Stairwell landing",
      tile: { x: 18, y: 43 },
    },
    stand: { x: 17, y: 43 },
  },
];

function chebyshevDistance(errand: HiveOfficeErrand): number {
  return Math.max(
    Math.abs(errand.stand.x - errand.object.tile.x),
    Math.abs(errand.stand.y - errand.object.tile.y),
  );
}

function pathFromReception(errand: HiveOfficeErrand) {
  const start = { x: OFFICEPLACE_RECEPTIONIST.x, y: OFFICEPLACE_RECEPTIONIST.y };
  return findPathTiles(start, errand.stand, createOfficeplaceOpenWalkGrid());
}

describe("officeplace idle errand layout", () => {
  it("AC-1/AC-2: exposes the exact frozen eight-row object and stand catalog", () => {
    expect(OFFICEPLACE_ERRANDS).toEqual(EXPECTED_ERRANDS);
  });

  it("AC-1: supplies unique non-empty object metadata and integer image-space coordinates", () => {
    const objectIds = OFFICEPLACE_ERRANDS.map((errand) => errand.object.id);
    const objectLabels = OFFICEPLACE_ERRANDS.map((errand) => errand.object.label);

    expect(new Set(objectIds).size).toBe(OFFICEPLACE_ERRANDS.length);
    expect(new Set(objectLabels).size).toBe(OFFICEPLACE_ERRANDS.length);
    for (const errand of OFFICEPLACE_ERRANDS) {
      expect(errand.object.id.trim()).not.toBe("");
      expect(errand.object.label.trim()).not.toBe("");
      for (const [kind, tile] of [
        ["object", errand.object.tile],
        ["stand", errand.stand],
      ] as const) {
        expect({
          id: errand.id,
          kind,
          integer: Number.isInteger(tile.x) && Number.isInteger(tile.y),
          inBounds:
            tile.x >= 0 &&
            tile.x < OFFICEPLACE_MAP.width &&
            tile.y >= 0 &&
            tile.y < OFFICEPLACE_MAP.height,
        }).toEqual({ id: errand.id, kind, integer: true, inBounds: true });
      }
    }
  });

  it("AC-3: keeps every stand exactly one Chebyshev tile from its object anchor", () => {
    for (const errand of OFFICEPLACE_ERRANDS) {
      expect({ id: errand.id, distance: chebyshevDistance(errand) }).toEqual({
        id: errand.id,
        distance: 1,
      });
    }
  });

  it("AC-4: keeps every stand walkable and standable", () => {
    const grid = createOfficeplaceOpenWalkGrid();
    expect(grid.isStandable).toBeDefined();

    for (const errand of OFFICEPLACE_ERRANDS) {
      expect({
        id: errand.id,
        walkable: grid.isWalkable(errand.stand.x, errand.stand.y),
        standable: grid.isStandable!(errand.stand.x, errand.stand.y),
      }).toEqual({ id: errand.id, walkable: true, standable: true });
    }
  });

  it("AC-5: paths from reception start and finish at the requested tiles without snapping", () => {
    const start = { x: OFFICEPLACE_RECEPTIONIST.x, y: OFFICEPLACE_RECEPTIONIST.y };

    for (const errand of OFFICEPLACE_ERRANDS) {
      const path = pathFromReception(errand);
      expect({ id: errand.id, first: path[0], last: path.at(-1) }).toEqual({
        id: errand.id,
        first: start,
        last: errand.stand,
      });
      expect(path.length).toBeGreaterThan(1);
      for (let index = 1; index < path.length; index += 1) {
        const previous = path[index - 1]!;
        const current = path[index]!;
        expect({
          id: errand.id,
          index,
          cardinalStep: Math.abs(current.x - previous.x) + Math.abs(current.y - previous.y),
        }).toEqual({ id: errand.id, index, cardinalStep: 1 });
      }
    }
  });

  it("AC-6: defines a reachable watering interaction beside the lobby plant", () => {
    const watering = OFFICEPLACE_ERRANDS.find((errand) => errand.id === "plant-watering");
    expect(watering).toBeDefined();

    const grid = createOfficeplaceOpenWalkGrid();
    const path = pathFromReception(watering!);
    expect({
      objectIsPlant: watering!.object.label.toLowerCase().includes("plant"),
      statusIsWatering: watering!.label.toLowerCase().includes("watering"),
      distance: chebyshevDistance(watering!),
      stand: watering!.stand,
      walkable: grid.isWalkable(watering!.stand.x, watering!.stand.y),
      standable: grid.isStandable!(watering!.stand.x, watering!.stand.y),
      pathEnd: path.at(-1),
    }).toEqual({
      objectIsPlant: true,
      statusIsWatering: true,
      distance: 1,
      stand: { x: 2, y: 6 },
      walkable: true,
      standable: true,
      pathEnd: { x: 2, y: 6 },
    });
  });
});
