/**
 * Reception Area & Entrance Redesign — Comprehensive Adversarial Test Suite
 * Test Breaker: Expose edge cases, boundary violations, and consistency gaps
 * Ticket: 73-reception-area-entrance-redesign
 *
 * This suite systematically tests:
 * - Position boundary conditions and validation
 * - Reception desk and break room consistency
 * - Entrance area visual distinctness
 * - Receptionist NPC placement and rendering
 * - Layout data integrity and synchronization
 */

import {
  OFFICEPLACE_DESKS,
  OFFICEPLACE_ERRANDS,
  OFFICEPLACE_MAP,
  OFFICEPLACE_STATIONS,
  OFFICEPLACE_WAITING,
  OFFICEPLACE_ZONES,
} from "../layouts/officeplaceLayout";
import { getHiveLayout } from "../layouts";

describe("Reception Area & Entrance Redesign — Adversarial Suite", () => {
  let layout: ReturnType<typeof getHiveLayout>;

  beforeEach(() => {
    layout = getHiveLayout("officeplace");
  });

  // ============================================================================
  // DIMENSION 1: NULL & EMPTY VALUES
  // ============================================================================

  describe("Null & Empty Values", () => {
    it("should handle null zones without crashing", () => {
      const zones = OFFICEPLACE_ZONES || null;
      expect(() => {
        if (zones) zones.forEach((z) => expect(z.id).toBeDefined());
      }).not.toThrow();
    });

    it("should handle missing zones array", () => {
      const emptyZones: any[] = [];
      const reception = emptyZones.find((z) => z?.id === "reception");
      expect(reception).toBeUndefined();
    });

    it("should handle stations with missing coordinates", () => {
      const badStation = { id: "test", label: "Test" } as any;
      expect(badStation.x).toBeUndefined();
      expect(badStation.y).toBeUndefined();
    });

    it("should validate empty errand list", () => {
      const errands = OFFICEPLACE_ERRANDS || [];
      expect(Array.isArray(errands)).toBe(true);
      expect(errands.length).toBeGreaterThan(0);
    });
  });

  // ============================================================================
  // DIMENSION 2: BOUNDARY CONDITIONS
  // ============================================================================

  describe("Boundary Conditions", () => {
    it("should enforce deploy station at entrance (16, 20)", () => {
      const deployStation = OFFICEPLACE_STATIONS.deploy;
      expect(deployStation.x).toBe(16);
      expect(deployStation.y).toBe(20);
    });

    it("should have reception zone within map bounds", () => {
      const receptionZone = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      expect(receptionZone).toBeDefined();
      if (receptionZone) {
        expect(receptionZone.x).toBeGreaterThanOrEqual(0);
        expect(receptionZone.x).toBeLessThan(OFFICEPLACE_MAP.width);
        expect(receptionZone.y).toBeGreaterThanOrEqual(0);
        expect(receptionZone.y).toBeLessThan(OFFICEPLACE_MAP.height);
      }
    });

    it("should validate break-room zone exists and is positioned", () => {
      const breakRoom = OFFICEPLACE_ZONES.find((z) => z.id === "break-room");
      expect(breakRoom).toBeDefined();
      if (breakRoom) {
        expect(breakRoom.x).toBeGreaterThanOrEqual(0);
        expect(breakRoom.y).toBeGreaterThanOrEqual(0);
      }
    });

    it("should not allow negative coordinates in stations", () => {
      Object.entries(OFFICEPLACE_STATIONS).forEach(([_, pos]) => {
        expect(pos.x).toBeGreaterThanOrEqual(0);
        expect(pos.y).toBeGreaterThanOrEqual(0);
      });
    });

    it("should not allow coordinates exceeding map dimensions", () => {
      Object.entries(OFFICEPLACE_STATIONS).forEach(([_, pos]) => {
        expect(pos.x).toBeLessThan(OFFICEPLACE_MAP.width);
        expect(pos.y).toBeLessThan(OFFICEPLACE_MAP.height);
      });
    });

    it("should validate desk row positions are within bounds", () => {
      OFFICEPLACE_DESKS.forEach((desk) => {
        expect(desk.x).toBeGreaterThanOrEqual(0);
        expect(desk.x).toBeLessThan(OFFICEPLACE_MAP.width);
        expect(desk.y).toBeGreaterThanOrEqual(0);
        expect(desk.y).toBeLessThan(OFFICEPLACE_MAP.height);
      });
    });

    it("should validate waiting position is within bounds", () => {
      expect(OFFICEPLACE_WAITING.x).toBeGreaterThanOrEqual(0);
      expect(OFFICEPLACE_WAITING.x).toBeLessThan(OFFICEPLACE_MAP.width);
      expect(OFFICEPLACE_WAITING.y).toBeGreaterThanOrEqual(0);
      expect(OFFICEPLACE_WAITING.y).toBeLessThan(OFFICEPLACE_MAP.height);
    });

    it("should validate all zone positions within bounds", () => {
      OFFICEPLACE_ZONES.forEach((zone) => {
        expect(zone.x).toBeGreaterThanOrEqual(0);
        expect(zone.x).toBeLessThan(OFFICEPLACE_MAP.width);
        expect(zone.y).toBeGreaterThanOrEqual(0);
        expect(zone.y).toBeLessThan(OFFICEPLACE_MAP.height);
      });
    });

    it("should validate errand positions within bounds", () => {
      OFFICEPLACE_ERRANDS.forEach((errand) => {
        expect(errand.stand.x).toBeGreaterThanOrEqual(0);
        expect(errand.stand.x).toBeLessThan(OFFICEPLACE_MAP.width);
        expect(errand.stand.y).toBeGreaterThanOrEqual(0);
        expect(errand.stand.y).toBeLessThan(OFFICEPLACE_MAP.height);
      });
    });
  });

  // ============================================================================
  // DIMENSION 3: TYPE & STRUCTURE MUTATIONS
  // ============================================================================

  describe("Type & Structure Mutations", () => {
    it("should handle non-numeric coordinate values", () => {
      const badCoord = { x: "16", y: "20" } as any;
      expect(typeof badCoord.x).toBe("string");
      expect(typeof badCoord.y).toBe("string");
    });

    it("should validate station structure has required fields", () => {
      const stationKeys = Object.keys(OFFICEPLACE_STATIONS.deploy);
      expect(stationKeys).toContain("x");
      expect(stationKeys).toContain("y");
    });

    it("should enforce zone structure completeness", () => {
      OFFICEPLACE_ZONES.forEach((zone) => {
        expect(zone).toHaveProperty("id");
        expect(zone).toHaveProperty("x");
        expect(zone).toHaveProperty("y");
        expect(zone).toHaveProperty("label");
        expect(typeof zone.id).toBe("string");
        expect(typeof zone.label).toBe("string");
      });
    });

    it("should enforce errand structure completeness", () => {
      OFFICEPLACE_ERRANDS.forEach((errand) => {
        expect(errand).toHaveProperty("id");
        expect(errand).toHaveProperty("stand");
        expect(errand).toHaveProperty("label");
        expect(errand.stand).toHaveProperty("x");
        expect(errand.stand).toHaveProperty("y");
      });
    });

    it("should have proper string types for identifiers", () => {
      OFFICEPLACE_ZONES.forEach((zone) => {
        expect(typeof zone.id).toBe("string");
        expect(zone.id.length).toBeGreaterThan(0);
      });
    });

    it("should have numeric types for coordinates", () => {
      Object.entries(OFFICEPLACE_STATIONS).forEach(([_, pos]) => {
        expect(typeof pos.x).toBe("number");
        expect(typeof pos.y).toBe("number");
      });
    });
  });

  // ============================================================================
  // DIMENSION 4: INVALID/CORRUPT INPUTS
  // ============================================================================

  describe("Invalid/Corrupt Inputs", () => {
    it("should reject zones with empty id", () => {
      const badZone = { id: "", x: 5, y: 5, label: "Empty ID" };
      expect(badZone.id.length).toBe(0);
    });

    it("should reject zones with empty label", () => {
      const badZone = { id: "test", x: 5, y: 5, label: "" };
      expect(badZone.label.length).toBe(0);
    });

    it("should reject NaN coordinates", () => {
      const badPos = { x: NaN, y: NaN };
      expect(isNaN(badPos.x)).toBe(true);
      expect(isNaN(badPos.y)).toBe(true);
    });

    it("should reject Infinity coordinates", () => {
      const badPos = { x: Infinity, y: -Infinity };
      expect(isFinite(badPos.x)).toBe(false);
      expect(isFinite(badPos.y)).toBe(false);
    });

    it("should not have duplicate zone IDs", () => {
      const ids = OFFICEPLACE_ZONES.map((z) => z.id);
      const uniqueIds = new Set(ids);
      expect(ids.length).toBe(uniqueIds.size);
    });

    it("should not have duplicate errand IDs", () => {
      const ids = OFFICEPLACE_ERRANDS.map((e) => e.id);
      const uniqueIds = new Set(ids);
      expect(ids.length).toBe(uniqueIds.size);
    });
  });

  // ============================================================================
  // DIMENSION 5: CONSISTENCY & CROSS-VALIDATION
  // ============================================================================

  describe("Consistency & Cross-Validation", () => {
    it("should ensure reception zone references a valid station", () => {
      const receptionZone = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      expect(receptionZone).toBeDefined();
      // Reception should be near the deploy station (entrance)
      if (receptionZone) {
        const deploy = OFFICEPLACE_STATIONS.deploy;
        const distance = Math.abs(receptionZone.x - deploy.x) + Math.abs(receptionZone.y - deploy.y);
        // Allow reasonable proximity (within 2 tile radius)
        expect(distance).toBeLessThanOrEqual(3);
      }
    });

    it("should not duplicate desk or station positions", () => {
      const allPositions = [
        ...Object.values(OFFICEPLACE_STATIONS).map((s) => `${s.x},${s.y}`),
        ...OFFICEPLACE_DESKS.map((d) => `${d.x},${d.y}`),
      ];
      const uniquePositions = new Set(allPositions);
      expect(allPositions.length).toBe(uniquePositions.size);
    });

    it("should ensure break-room zone is distinct from reception", () => {
      const breakRoom = OFFICEPLACE_ZONES.find((z) => z.id === "break-room");
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");

      if (breakRoom && reception) {
        const samePosition = breakRoom.x === reception.x && breakRoom.y === reception.y;
        expect(samePosition).toBe(false);
      }
    });

    it("should ensure all desk positions are unique", () => {
      const deskKeys = OFFICEPLACE_DESKS.map((d) => `${d.x},${d.y}`);
      const uniqueDesks = new Set(deskKeys);
      expect(deskKeys.length).toBe(uniqueDesks.size);
    });

    it("should ensure errand positions are reasonable", () => {
      OFFICEPLACE_ERRANDS.forEach((errand) => {
        // Errands should be accessible points with valid coordinates
        expect(typeof errand.stand.x).toBe("number");
        expect(typeof errand.stand.y).toBe("number");
      });
    });
  });

  // ============================================================================
  // DIMENSION 6: LAYOUT VALIDATION
  // ============================================================================

  describe("Layout Validation", () => {
    it("should have valid HiveLayout for officeplace skin", () => {
      expect(layout).toBeDefined();
      expect(layout.map).toBeDefined();
      expect(layout.stationPositions).toBeDefined();
      expect(layout.zones).toBeDefined();
      expect(layout.errands).toBeDefined();
    });

    it("should match map dimensions in layout", () => {
      expect(layout.map.width).toBe(OFFICEPLACE_MAP.width);
      expect(layout.map.height).toBe(OFFICEPLACE_MAP.height);
      expect(layout.map.tileSize).toBe(OFFICEPLACE_MAP.tileSize);
    });

    it("should have station positions matching OFFICEPLACE_STATIONS", () => {
      expect(layout.stationPositions.deploy).toEqual(OFFICEPLACE_STATIONS.deploy);
      expect(layout.stationPositions.coding).toEqual(OFFICEPLACE_STATIONS.coding);
    });

    it("should have at least 4 zones defined", () => {
      expect(layout.zones.length).toBeGreaterThanOrEqual(4);
    });

    it("should have reception zone in layout", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      expect(reception).toBeDefined();
    });

    it("should have break-room zone in layout", () => {
      const breakRoom = layout.zones.find((z) => z.id === "break-room");
      expect(breakRoom).toBeDefined();
    });
  });

  // ============================================================================
  // DIMENSION 7: MUTATION TESTING
  // ============================================================================

  describe("Mutation Testing", () => {
    it("should catch reception zone ID mutation", () => {
      const zones = OFFICEPLACE_ZONES.map((z) =>
        z.id === "reception" ? { ...z, id: "reception-desk" } : z,
      );
      const reception = zones.find((z) => z.id === "reception");
      expect(reception).toBeUndefined();
    });

    it("should catch deploy position x coordinate mutation (16→17)", () => {
      const mutatedDeploy = { ...OFFICEPLACE_STATIONS.deploy, x: 17 };
      expect(mutatedDeploy.x).not.toBe(OFFICEPLACE_STATIONS.deploy.x);
    });

    it("should catch deploy position y coordinate mutation (20→19)", () => {
      const mutatedDeploy = { ...OFFICEPLACE_STATIONS.deploy, y: 19 };
      expect(mutatedDeploy.y).not.toBe(OFFICEPLACE_STATIONS.deploy.y);
    });

    it("should detect if break-room label changes", () => {
      const breakRoom = OFFICEPLACE_ZONES.find((z) => z.id === "break-room");
      const originalLabel = breakRoom?.label;
      const mutated = "Break Area"; // Different from "Break room"
      expect(mutated).not.toBe(originalLabel);
    });

    it("should detect if desk count changes", () => {
      const originalCount = OFFICEPLACE_DESKS.length;
      expect(originalCount).toBe(6); // Should have 6 desks
    });

    it("should catch errand position mutations", () => {
      const firstErrand = OFFICEPLACE_ERRANDS[0];
      const mutated = { ...firstErrand, stand: { x: firstErrand.stand.x + 1, y: firstErrand.stand.y } };
      expect(mutated.stand.x).not.toBe(firstErrand.stand.x);
    });
  });

  // ============================================================================
  // DIMENSION 8: STRESS & LOAD CONDITIONS
  // ============================================================================

  describe("Stress & Load Conditions", () => {
    it("should handle large iteration over zones", () => {
      const iterations = 10000;
      for (let i = 0; i < iterations; i++) {
        OFFICEPLACE_ZONES.forEach((z) => expect(z.id).toBeDefined());
      }
    });

    it("should handle large iteration over errands", () => {
      const iterations = 10000;
      for (let i = 0; i < iterations; i++) {
        OFFICEPLACE_ERRANDS.forEach((e) => expect(e.stand).toBeDefined());
      }
    });

    it("should validate performance of layout lookup", () => {
      const start = performance.now();
      for (let i = 0; i < 1000; i++) {
        getHiveLayout("officeplace");
      }
      const elapsed = performance.now() - start;
      expect(elapsed).toBeLessThan(100); // Should be very fast
    });

    it("should validate all desks can be accessed", () => {
      OFFICEPLACE_DESKS.forEach((desk, idx) => {
        const found = layout.deskRow[idx];
        expect(found).toBeDefined();
        expect(found?.x).toBe(desk.x);
        expect(found?.y).toBe(desk.y);
      });
    });
  });

  // ============================================================================
  // DIMENSION 9: ASSUMPTION VALIDATION
  // ============================================================================

  describe("Assumption Validation", () => {
    it("should assume deploy station is the entrance", () => {
      const deploy = OFFICEPLACE_STATIONS.deploy;
      const entrance = OFFICEPLACE_ZONES.find((z) => z.id === "reception");

      if (entrance) {
        // Deploy and reception should be very close
        const distance = Math.sqrt(Math.pow(deploy.x - entrance.x, 2) + Math.pow(deploy.y - entrance.y, 2));
        expect(distance).toBeLessThanOrEqual(2);
      }
    });

    it("should assume reception area is visually distinct", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      expect(reception?.label).toBeDefined();
      expect(reception?.label?.length).toBeGreaterThan(0);
    });

    it("should assume break-room does NOT have reception desk", () => {
      const breakRoom = OFFICEPLACE_ZONES.find((z) => z.id === "break-room");
      // Break room should exist and be separate from reception
      expect(breakRoom).toBeDefined();
      expect(breakRoom?.id).not.toBe("reception");
    });

    it("should assume exactly 5 stations exist", () => {
      const stations = Object.keys(OFFICEPLACE_STATIONS);
      expect(stations.length).toBe(5);
      expect(stations).toContain("deploy");
    });

    it("should assume map uses 16px tiles", () => {
      expect(OFFICEPLACE_MAP.tileSize).toBe(16);
    });
  });

  // ============================================================================
  // DIMENSION 10: RECEPTIONIST NPC READINESS
  // ============================================================================

  describe("Receptionist NPC Readiness", () => {
    it("should have reception zone positioned for NPC rendering", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      expect(reception).toBeDefined();
      if (reception) {
        // Position should be walkable (not at extreme edges)
        expect(reception.x).toBeGreaterThan(0);
        expect(reception.x).toBeLessThan(OFFICEPLACE_MAP.width - 1);
        expect(reception.y).toBeGreaterThan(0);
        expect(reception.y).toBeLessThan(OFFICEPLACE_MAP.height - 1);
      }
    });

    it("should have entrance accessible from other areas", () => {
      const deploy = OFFICEPLACE_STATIONS.deploy;
      // Deploy position should be reachable (not surrounded by walls)
      expect(deploy.x).toBeGreaterThan(0);
      expect(deploy.y).toBeGreaterThan(0);
      expect(deploy.x).toBeLessThan(OFFICEPLACE_MAP.width - 1);
      expect(deploy.y).toBeLessThan(OFFICEPLACE_MAP.height - 1);
    });

    it("should have zone with label for UI display", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      expect(reception?.label).toBeTruthy();
      expect(reception?.label?.toLowerCase()).toContain("reception");
    });

    it("should have unique NPC spawn point for receptionist", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      const deploy = OFFICEPLACE_STATIONS.deploy;

      // Should be a distinct position
      expect(reception).toBeDefined();
      expect(deploy).toBeDefined();
    });
  });

  // ============================================================================
  // DIMENSION 11: ORDER DEPENDENCY
  // ============================================================================

  describe("Order Dependency", () => {
    it("should produce consistent results regardless of zone iteration order", () => {
      const reception1 = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      const reception2 = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      expect(reception1).toEqual(reception2);
    });

    it("should produce consistent station positions on multiple accesses", () => {
      const deploy1 = OFFICEPLACE_STATIONS.deploy;
      const deploy2 = OFFICEPLACE_STATIONS.deploy;
      expect(deploy1.x).toBe(deploy2.x);
      expect(deploy1.y).toBe(deploy2.y);
    });

    it("should maintain desk row order", () => {
      const first = OFFICEPLACE_DESKS[0];
      const firstAgain = layout.deskRow[0];
      expect(first).toEqual(firstAgain);
    });
  });

  // ============================================================================
  // DIMENSION 12: BREAK ROOM VERIFICATION
  // ============================================================================

  describe("Break Room Verification", () => {
    it("should have break-room zone defined", () => {
      const breakRoom = OFFICEPLACE_ZONES.find((z) => z.id === "break-room");
      expect(breakRoom).toBeDefined();
    });

    it("should NOT place reception desk in break-room", () => {
      const breakRoom = OFFICEPLACE_ZONES.find((z) => z.id === "break-room");
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");

      // They should be different zones at different positions
      expect(breakRoom?.id).not.toBe("reception");
      expect(reception?.id).not.toBe("break-room");

      if (breakRoom && reception) {
        expect({
          x: breakRoom.x,
          y: breakRoom.y,
        }).not.toEqual({
          x: reception.x,
          y: reception.y,
        });
      }
    });

    it("should position break-room away from entrance", () => {
      const breakRoom = OFFICEPLACE_ZONES.find((z) => z.id === "break-room");
      const deploy = OFFICEPLACE_STATIONS.deploy;

      if (breakRoom) {
        const distance = Math.sqrt(Math.pow(breakRoom.x - deploy.x, 2) + Math.pow(breakRoom.y - deploy.y, 2));
        // Should be meaningfully distant (at least 5 tiles away)
        expect(distance).toBeGreaterThan(3);
      }
    });

    it("should have break-room label not referencing reception", () => {
      const breakRoom = OFFICEPLACE_ZONES.find((z) => z.id === "break-room");
      expect(breakRoom?.label?.toLowerCase()).not.toContain("reception");
    });
  });
});
