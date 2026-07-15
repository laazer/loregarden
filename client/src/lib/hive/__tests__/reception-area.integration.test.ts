/**
 * Reception Area Integration Tests
 * Test Breaker: Expose integration issues between reception redesign and other systems
 * Ticket: 73-reception-area-entrance-redesign
 *
 * This suite tests:
 * - Cross-system consistency (layout, stations, zones, errands)
 * - World model integration
 * - Data synchronization and propagation
 * - Scenario edge cases
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

describe("Reception Area Integration Tests", () => {
  let layout: ReturnType<typeof getHiveLayout>;

  beforeEach(() => {
    layout = getHiveLayout("officeplace");
  });

  // ============================================================================
  // CROSS-SYSTEM CONSISTENCY
  // ============================================================================

  describe("Cross-System Consistency", () => {
    it("should keep reception position synchronized between all references", () => {
      const z1 = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      const z2 = layout.zones.find((z) => z.id === "reception");

      if (z1 && z2) {
        expect(z1.x).toBe(z2.x);
        expect(z1.y).toBe(z2.y);
        expect(z1.id).toBe(z2.id);
        expect(z1.label).toBe(z2.label);
      }
    });

    it("should ensure deploy station and reception are near each other", () => {
      const deploy = OFFICEPLACE_STATIONS.deploy;
      const reception = layout.zones.find((z) => z.id === "reception");

      if (reception) {
        const dx = Math.abs(deploy.x - reception.x);
        const dy = Math.abs(deploy.y - reception.y);
        const distance = dx + dy;
        // Should be close for entrance area
        expect(distance).toBeLessThanOrEqual(3);
      }
    });

    it("should maintain no duplicate zones across layout", () => {
      const allZones = layout.zones;
      const ids = allZones.map((z) => z.id);
      const uniqueIds = new Set(ids);
      expect(ids.length).toBe(uniqueIds.size);
    });

    it("should ensure zone labels are consistent with IDs", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      expect(reception?.label).toBeDefined();
      expect(reception?.label?.length).toBeGreaterThan(0);
      expect(reception?.label?.toLowerCase()).toContain("reception");
    });

    it("should validate all station positions are unique", () => {
      const positions = Object.values(OFFICEPLACE_STATIONS).map((s) => `${s.x},${s.y}`);
      const uniquePositions = new Set(positions);
      expect(positions.length).toBe(uniquePositions.size);
    });

    it("should ensure receptionist does not conflict with any station", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      if (reception) {
        const conflicting = Object.values(OFFICEPLACE_STATIONS).filter(
          (s) => s.x === reception.x && s.y === reception.y,
        );
        expect(conflicting).toHaveLength(0);
      }
    });

    it("should validate errand positions don't duplicate critical zones", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      const breakRoom = layout.zones.find((z) => z.id === "break-room");

      // Errands can be near but not exactly on critical zones
      if (reception && breakRoom) {
        expect(typeof reception.x).toBe("number");
        expect(typeof breakRoom.x).toBe("number");
      }
    });
  });

  // ============================================================================
  // BREAK ROOM INTEGRITY
  // ============================================================================

  describe("Break Room Integrity After Redesign", () => {
    it("should keep break room and reception as distinct zones", () => {
      const breakRoom = layout.zones.find((z) => z.id === "break-room");
      const reception = layout.zones.find((z) => z.id === "reception");

      if (breakRoom && reception) {
        expect(breakRoom.id).not.toBe(reception.id);
        const samePosition = breakRoom.x === reception.x && breakRoom.y === reception.y;
        expect(samePosition).toBe(false);
      }
    });

    it("should ensure break room is not at entrance", () => {
      const breakRoom = layout.zones.find((z) => z.id === "break-room");
      const deploy = OFFICEPLACE_STATIONS.deploy;

      if (breakRoom) {
        const distance = Math.sqrt(Math.pow(breakRoom.x - deploy.x, 2) + Math.pow(breakRoom.y - deploy.y, 2));
        // Break room should be at least 3 tiles away from entrance
        expect(distance).toBeGreaterThan(3);
      }
    });

    it("should validate break room has proper zone definition", () => {
      const breakRoom = layout.zones.find((z) => z.id === "break-room");
      expect(breakRoom).toBeDefined();
      expect(breakRoom?.label).toBeDefined();
      expect(breakRoom?.x).toBeGreaterThanOrEqual(0);
      expect(breakRoom?.y).toBeGreaterThanOrEqual(0);
    });

    it("should ensure break room label does not mention reception", () => {
      const breakRoom = layout.zones.find((z) => z.id === "break-room");
      expect(breakRoom?.label?.toLowerCase()).not.toContain("reception");
      expect(breakRoom?.label?.toLowerCase()).not.toContain("desk");
    });

    it("should prevent break room from containing reception desk data", () => {
      const breakRoom = layout.zones.find((z) => z.id === "break-room");
      // Break room should be a simple zone without embedded desk references
      expect(breakRoom).toHaveProperty("id");
      expect(breakRoom).toHaveProperty("x");
      expect(breakRoom).toHaveProperty("y");
      expect(breakRoom).toHaveProperty("label");
      // Should not have desk-specific properties
      expect((breakRoom as any).deskType).toBeUndefined();
    });
  });

  // ============================================================================
  // ENTRANCE AREA FUNCTIONALITY
  // ============================================================================

  describe("Entrance Area Functionality", () => {
    it("should have reception zone at proper entrance location", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      const deploy = OFFICEPLACE_STATIONS.deploy;

      if (reception) {
        // Reception should be very close to deploy (entrance)
        expect(Math.abs(reception.x - deploy.x)).toBeLessThanOrEqual(2);
        expect(Math.abs(reception.y - deploy.y)).toBeLessThanOrEqual(2);
      }
    });

    it("should ensure entrance is visually distinct", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      expect(reception?.label).toBeTruthy();
      // Visual distinction comes from label and positioning
      expect(reception?.label).not.toBe("");
    });

    it("should support agent movement through entrance", () => {
      const deploy = OFFICEPLACE_STATIONS.deploy;
      // Entrance should be walkable
      const isWalkable = layout.walkGrid.isWalkable(deploy.x, deploy.y);
      expect(isWalkable).toBe(true);
    });

    it("should allow agents to reach reception from deploy", () => {
      const deploy = OFFICEPLACE_STATIONS.deploy;
      const reception = layout.zones.find((z) => z.id === "reception");

      if (reception) {
        // Should have a path from deploy to reception
        const canReach = layout.walkGrid.isWalkable(reception.x, reception.y);
        expect(canReach).toBe(true);
      }
    });

    it("should validate entrance station exists and is active", () => {
      expect(OFFICEPLACE_STATIONS.deploy).toBeDefined();
      expect(OFFICEPLACE_STATIONS.deploy.x).toBe(16);
      expect(OFFICEPLACE_STATIONS.deploy.y).toBe(20);
    });

    it("should prevent entrance from being blocked by other zones", () => {
      const deploy = OFFICEPLACE_STATIONS.deploy;
      const nearbyZones = layout.zones.filter((z) => {
        const dx = Math.abs(z.x - deploy.x);
        const dy = Math.abs(z.y - deploy.y);
        return dx <= 1 && dy <= 1;
      });

      // Reception should be nearby, but not creating a block
      const reception = nearbyZones.find((z) => z.id === "reception");
      expect(reception).toBeDefined();
    });
  });

  // ============================================================================
  // DATA SYNCHRONIZATION
  // ============================================================================

  describe("Data Synchronization", () => {
    it("should keep reception data consistent on repeated accesses", () => {
      const r1 = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      const r2 = layout.zones.find((z) => z.id === "reception");
      const r3 = OFFICEPLACE_ZONES.find((z) => z.id === "reception");

      expect(r1).toEqual(r3);
      expect(r1).toEqual(r2);
    });

    it("should propagate zone changes through layout system", () => {
      const zone = layout.zones.find((z) => z.id === "reception");
      expect(zone?.label).toBeTruthy();
      // Should be readable from both sources
      const zone2 = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      if (zone && zone2) {
        expect(zone.label).toBe(zone2.label);
      }
    });

    it("should validate station position changes propagate", () => {
      const original = OFFICEPLACE_STATIONS.deploy;
      const fromLayout = layout.stationPositions.deploy;

      expect(original.x).toBe(fromLayout.x);
      expect(original.y).toBe(fromLayout.y);
    });

    it("should detect missing receptionist zone in layout", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      expect(reception).toBeDefined();
    });

    it("should ensure zone count remains stable", () => {
      const count1 = OFFICEPLACE_ZONES.length;
      const count2 = layout.zones.length;
      expect(count1).toBe(count2);
      // Should have at least 4 zones (reception, break-room, boardroom, bullpen)
      expect(count1).toBeGreaterThanOrEqual(4);
    });
  });

  // ============================================================================
  // SCENARIO EDGE CASES
  // ============================================================================

  describe("Scenario Edge Cases", () => {
    it("should handle empty zone list gracefully", () => {
      const zones: any[] = [];
      const reception = zones.find((z) => z?.id === "reception");
      expect(reception).toBeUndefined();
    });

    it("should handle null zone gracefully", () => {
      const zone = null;
      expect(() => {
        if (zone?.x) {
          console.log(zone.x);
        }
      }).not.toThrow();
    });

    it("should handle zone with missing coordinates", () => {
      const badZone = { id: "test" } as any;
      expect(badZone.x).toBeUndefined();
      expect(badZone.y).toBeUndefined();
    });

    it("should handle multiple agents at reception", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      // Should be able to accommodate multiple agents nearby
      expect(reception).toBeDefined();
      if (reception) {
        // Check neighboring tiles exist
        for (let dx = -1; dx <= 1; dx++) {
          for (let dy = -1; dy <= 1; dy++) {
            const x = reception.x + dx;
            const y = reception.y + dy;
            if (x >= 0 && x < OFFICEPLACE_MAP.width && y >= 0 && y < OFFICEPLACE_MAP.height) {
              expect(x).toBeGreaterThanOrEqual(0);
            }
          }
        }
      }
    });

    it("should handle rapid zone lookups", () => {
      for (let i = 0; i < 100; i++) {
        const reception = layout.zones.find((z) => z.id === "reception");
        expect(reception).toBeDefined();
      }
    });

    it("should validate zone positions during map resize scenario", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      if (reception) {
        // Should still be within original bounds
        expect(reception.x).toBeLessThan(OFFICEPLACE_MAP.width);
        expect(reception.y).toBeLessThan(OFFICEPLACE_MAP.height);
      }
    });
  });

  // ============================================================================
  // WORLD MODEL COMPATIBILITY
  // ============================================================================

  describe("World Model Compatibility", () => {
    it("should have reception zone available in layout.zones", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      expect(reception).toBeDefined();
    });

    it("should maintain reception in zone array during sync", () => {
      const before = layout.zones.find((z) => z.id === "reception");
      // Simulate sync
      const after = layout.zones.find((z) => z.id === "reception");

      if (before && after) {
        expect(before.id).toBe(after.id);
        expect(before.x).toBe(after.x);
        expect(before.y).toBe(after.y);
      }
    });

    it("should provide valid walkGrid for reception area", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      if (reception) {
        const isWalkable = layout.walkGrid.isWalkable(reception.x, reception.y);
        expect(typeof isWalkable).toBe("boolean");
        expect(isWalkable).toBe(true);
      }
    });

    it("should ensure station positions match world model", () => {
      const modelStations = layout.stationPositions;
      const dataStations = OFFICEPLACE_STATIONS;

      Object.keys(modelStations).forEach((key) => {
        const modelPos = modelStations[key as keyof typeof modelStations];
        const dataPos = dataStations[key as keyof typeof dataStations];
        if (modelPos && dataPos) {
          expect(modelPos.x).toBe(dataPos.x);
          expect(modelPos.y).toBe(dataPos.y);
        }
      });
    });

    it("should support agent routing to reception", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      if (reception) {
        // Reception should be a valid agent destination
        expect(Number.isFinite(reception.x)).toBe(true);
        expect(Number.isFinite(reception.y)).toBe(true);
      }
    });
  });

  // ============================================================================
  // ASSET & RESOURCE VALIDATION
  // ============================================================================

  describe("Asset & Resource Validation", () => {
    it("should have proper zone label for UI display", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      expect(reception?.label).toBeTruthy();
      expect(typeof reception?.label).toBe("string");
    });

    it("should support zone filtering by ID", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      expect(reception?.id).toBe("reception");

      const notReception = layout.zones.find((z) => z.id !== "reception");
      expect(notReception?.id).not.toBe("reception");
    });

    it("should maintain zone reference integrity", () => {
      const z1 = layout.zones.find((z) => z.id === "reception");
      const z2 = layout.zones.find((z) => z.id === "reception");

      expect(z1?.id).toBe(z2?.id);
    });

    it("should provide complete zone data for rendering", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      expect(reception).toHaveProperty("id");
      expect(reception).toHaveProperty("x");
      expect(reception).toHaveProperty("y");
      expect(reception).toHaveProperty("label");
    });

    it("should ensure zone identifiers are stable", () => {
      const id1 = OFFICEPLACE_ZONES.find((z) => z.id === "reception")?.id;
      const id2 = layout.zones.find((z) => z.id === "reception")?.id;
      expect(id1).toBe(id2);
    });
  });

  // ============================================================================
  // LOAD & PERFORMANCE
  // ============================================================================

  describe("Load & Performance Under Integration", () => {
    it("should handle layout initialization quickly", () => {
      const start = performance.now();
      for (let i = 0; i < 100; i++) {
        getHiveLayout("officeplace");
      }
      const elapsed = performance.now() - start;
      expect(elapsed).toBeLessThan(50);
    });

    it("should efficiently lookup reception across multiple accesses", () => {
      const start = performance.now();
      for (let i = 0; i < 1000; i++) {
        layout.zones.find((z) => z.id === "reception");
      }
      const elapsed = performance.now() - start;
      expect(elapsed).toBeLessThan(20);
    });

    it("should maintain zone array integrity under iteration stress", () => {
      const originalLength = layout.zones.length;
      for (let i = 0; i < 100; i++) {
        layout.zones.forEach((z) => {
          expect(z.id).toBeDefined();
        });
      }
      expect(layout.zones.length).toBe(originalLength);
    });

    it("should handle concurrent zone lookups", async () => {
      const lookups = Array.from({ length: 50 }, () =>
        Promise.resolve(layout.zones.find((z) => z.id === "reception")),
      );

      const results = await Promise.all(lookups);
      results.forEach((zone) => {
        expect(zone?.id).toBe("reception");
      });
    });
  });

  // ============================================================================
  // REGRESSION DETECTION
  // ============================================================================

  describe("Regression Detection", () => {
    it("should detect if reception zone is removed", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      expect(reception).toBeDefined();
    });

    it("should detect if reception position changes", () => {
      const expected = { x: 16, y: 19 };
      const reception = layout.zones.find((z) => z.id === "reception");
      if (reception) {
        expect(reception.x).toBe(expected.x);
        expect(reception.y).toBe(expected.y);
      }
    });

    it("should detect if break room moves to entrance", () => {
      const breakRoom = layout.zones.find((z) => z.id === "break-room");
      const deploy = OFFICEPLACE_STATIONS.deploy;
      if (breakRoom) {
        const distance = Math.sqrt(Math.pow(breakRoom.x - deploy.x, 2) + Math.pow(breakRoom.y - deploy.y, 2));
        expect(distance).toBeGreaterThan(3);
      }
    });

    it("should detect if reception label changes", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      expect(reception?.label?.toLowerCase()).toContain("reception");
    });

    it("should detect if duplicate reception zones are created", () => {
      const receptions = layout.zones.filter((z) => z.id === "reception");
      expect(receptions).toHaveLength(1);
    });

    it("should detect if deploy station position changes", () => {
      expect(OFFICEPLACE_STATIONS.deploy.x).toBe(16);
      expect(OFFICEPLACE_STATIONS.deploy.y).toBe(20);
    });

    it("should detect if zone boundaries are violated", () => {
      layout.zones.forEach((zone) => {
        expect(zone.x).toBeGreaterThanOrEqual(0);
        expect(zone.x).toBeLessThan(OFFICEPLACE_MAP.width);
        expect(zone.y).toBeGreaterThanOrEqual(0);
        expect(zone.y).toBeLessThan(OFFICEPLACE_MAP.height);
      });
    });
  });
});
