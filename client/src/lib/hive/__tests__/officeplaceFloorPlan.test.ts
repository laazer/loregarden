import { createOfficeplaceOpenWalkGrid, createOfficeplaceWalkGrid, getHiveLayout } from "../layouts";
import {
  OFFICEPLACE_DOORS,
  OFFICEPLACE_FLOOR,
  OFFICEPLACE_FLOOR_DESKS,
  OFFICEPLACE_PROPS,
  OFFICEPLACE_ROOMS,
  isFloorBlocked,
} from "../layouts/officeplaceFloorPlan";
import { OFFICEPLACE_PROP_URLS } from "../layouts/officeplaceProps";
import {
  OFFICEPLACE_DESKS,
  OFFICEPLACE_ERRANDS,
  OFFICEPLACE_RECEPTIONIST,
  OFFICEPLACE_STATIONS,
  OFFICEPLACE_WAITING,
} from "../layouts/officeplaceLayout";

type Tile = { x: number; y: number };

/** Every tile reachable from `start` by 4-way movement over `walkable`. */
function reachableFrom(start: Tile, walkable: (x: number, y: number) => boolean): Set<string> {
  const seen = new Set<string>([`${start.x},${start.y}`]);
  const queue: Tile[] = [start];
  while (queue.length) {
    const { x, y } = queue.shift()!;
    for (const [dx, dy] of [
      [1, 0],
      [-1, 0],
      [0, 1],
      [0, -1],
    ]) {
      const nx = x + dx;
      const ny = y + dy;
      const key = `${nx},${ny}`;
      if (seen.has(key) || !walkable(nx, ny)) continue;
      seen.add(key);
      queue.push({ x: nx, y: ny });
    }
  }
  return seen;
}

describe("officeplace floor plan", () => {
  const grid = createOfficeplaceWalkGrid();

  it("blocks the building shell and room walls, but not doorways", () => {
    expect(isFloorBlocked(0, 0)).toBe(true);
    expect(isFloorBlocked(OFFICEPLACE_FLOOR.width - 1, 10)).toBe(true);

    const accounting = OFFICEPLACE_ROOMS.find((r) => r.id === "accounting")!;
    // Accounting's right wall is solid...
    expect(isFloorBlocked(accounting.x + accounting.w - 1, accounting.y + 1)).toBe(true);
    // ...except where its door is punched through.
    expect(isFloorBlocked(17, 37)).toBe(false);
  });

  it("seats every occupant on a walkable tile outside their own desk", () => {
    for (const desk of OFFICEPLACE_FLOOR_DESKS) {
      expect({ desk: desk.id, blocked: isFloorBlocked(desk.seat.x, desk.seat.y) }).toEqual({
        desk: desk.id,
        blocked: false,
      });
    }
  });

  it("encloses Pam on three sides with an open front", () => {
    const pam = OFFICEPLACE_FLOOR_DESKS.find((d) => d.id === "pam")!;
    expect(pam.wrap).toBe(true);
    // Back and both sides are desk; the seat sits in the hole.
    expect(isFloorBlocked(pam.seat.x, pam.y)).toBe(true);
    expect(isFloorBlocked(pam.x, pam.seat.y)).toBe(true);
    expect(isFloorBlocked(pam.x + pam.w - 1, pam.seat.y)).toBe(true);
    expect(isFloorBlocked(pam.seat.x, pam.seat.y)).toBe(false);
    // Front edge is open so she can be reached.
    expect(isFloorBlocked(pam.seat.x, pam.y + pam.h - 1)).toBe(false);
  });

  it("builds the reception desk from desk sprites, not a rug or a drawn box", () => {
    const pam = OFFICEPLACE_FLOOR_DESKS.find((d) => d.id === "pam")!;
    const piece = (id: string) => OFFICEPLACE_PROPS.find((p) => p.id === id)!;
    const [backL, backR, wingL, wingR] = [
      piece("pam-counter-l"),
      piece("pam-counter-r"),
      piece("pam-wing-l"),
      piece("pam-wing-r"),
    ];

    // Every piece is desk art. A floor covering here would read as a carpet.
    for (const p of [backL, backR, wingL, wingR]) {
      expect(p.sprite).toBe("desk-wood");
      expect(OFFICEPLACE_PROP_URLS[p.sprite]).toBeTruthy();
    }
    // The back run is continuous and the wings turn inward to enclose the seat.
    expect(backL.x + backL.w).toBe(backR.x);
    expect(wingL.rotate).toBe(90);
    expect(wingR.rotate).toBe(270);
    // It is the largest desk on the floor — it's the one that wraps its occupant.
    const widest = Math.max(...OFFICEPLACE_FLOOR_DESKS.filter((d) => !d.wrap).map((d) => d.w));
    expect(pam.w).toBeGreaterThan(widest);
    // Pam sits between the wings, below the back run.
    expect(pam.seat.x).toBeGreaterThanOrEqual(wingL.x + wingL.w);
    expect(pam.seat.x).toBeLessThan(wingR.x);
    expect(pam.seat.y).toBeGreaterThanOrEqual(backL.y + backL.h - 1);
  });

  it("leaves reception open to the floor rather than walling it off", () => {
    const reception = OFFICEPLACE_ROOMS.find((r) => r.id === "reception")!;
    expect(reception.kind).toBe("open");
    // An open area has no wall band, so its edges must not block.
    expect(isFloorBlocked(reception.x + reception.w - 1, reception.y + 5)).toBe(false);
    // ...and therefore needs no door punched through it.
    const doors = OFFICEPLACE_DOORS.filter(
      (d) =>
        d.x >= reception.x &&
        d.x < reception.x + reception.w &&
        d.y >= reception.y &&
        d.y < reception.y + reception.h,
    );
    expect(doors).toEqual([]);
  });

  it("connects every roaming station, agent desk and errand to reception", () => {
    // The sim walks the image-aligned room grid (createOfficeplaceOpenWalkGrid): rooms
    // bridged by doorways, with the foundation strip excluded. `testing` lives in the
    // MDR room, which reaches the floor through the elevator strip down the right edge.
    const openGrid = createOfficeplaceOpenWalkGrid();
    const reachable = reachableFrom(OFFICEPLACE_RECEPTIONIST, openGrid.isWalkable);
    const targets: Array<[string, Tile]> = [
      ...Object.entries(OFFICEPLACE_STATIONS).map(([id, t]): [string, Tile] => [`station:${id}`, t]),
      ...OFFICEPLACE_DESKS.map((t, i): [string, Tile] => [`desk:${i}`, t]),
      ...OFFICEPLACE_ERRANDS.map((e): [string, Tile] => [`errand:${e.id}`, e.stand]),
      ["waiting", OFFICEPLACE_WAITING],
    ];

    const unreachable = targets
      .filter(([, t]) => !reachable.has(`${t.x},${t.y}`))
      .map(([id]) => id);
    expect(unreachable).toEqual([]);
  });

  it("reaches the MDR room from the floor through the elevator strip", () => {
    // MDR is on the roaming grid via the right-edge elevator strip, so the tester
    // agent can path to the testing station where the QA staff sit.
    const openGrid = createOfficeplaceOpenWalkGrid();
    const reachable = reachableFrom(OFFICEPLACE_RECEPTIONIST, openGrid.isWalkable);
    expect(reachable.has(`${OFFICEPLACE_STATIONS.testing.x},${OFFICEPLACE_STATIONS.testing.y}`)).toBe(
      true,
    );
  });

  it("keeps every room inside the building shell", () => {
    for (const room of OFFICEPLACE_ROOMS) {
      expect({
        room: room.id,
        fits:
          room.x >= 0 &&
          room.y >= 0 &&
          room.x + room.w <= OFFICEPLACE_FLOOR.width &&
          room.y + room.h <= OFFICEPLACE_FLOOR.height,
      }).toEqual({ room: room.id, fits: true });
    }
  });

  it("resolves art for every prop and desk sprite", () => {
    const unresolved = [
      ...OFFICEPLACE_PROPS.map((p) => [p.id, p.sprite] as const),
      ...OFFICEPLACE_FLOOR_DESKS.map((d) => [d.id, d.sprite ?? "desk-wood"] as const),
    ]
      .filter(([, sprite]) => !OFFICEPLACE_PROP_URLS[sprite])
      .map(([id, sprite]) => `${id} → ${sprite}`);
    expect(unresolved).toEqual([]);
  });

  it("keeps furniture from blocking the tiles agents stand on", () => {
    const seats = OFFICEPLACE_FLOOR_DESKS.map((d) => d.seat);
    const blocked = seats.filter((s) => isFloorBlocked(s.x, s.y));
    expect(blocked).toEqual([]);
  });

  it("exposes the plan through the layout API", () => {
    const layout = getHiveLayout("officeplace");
    expect(layout.rooms).toBe(OFFICEPLACE_ROOMS);
    expect(layout.floorDesks).toBe(OFFICEPLACE_FLOOR_DESKS);
    expect(grid.width).toBe(OFFICEPLACE_FLOOR.width);
  });
});
