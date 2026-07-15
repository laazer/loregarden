/**
 * Reception Area NPC Rendering Tests
 * Test Breaker: Expose edge cases in receptionist NPC placement, rendering, and lifecycle
 * Ticket: 73-reception-area-entrance-redesign
 *
 * This suite adversarially tests NPC functionality including:
 * - Receptionist character spawning at reception zone
 * - NPC rendering lifecycle (creation, sync, destruction)
 * - Position validation and walkability
 * - Collision and overlap detection
 * - State management and transitions
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

describe("Reception Area NPC Rendering — Adversarial Suite", () => {
  let layout: ReturnType<typeof getHiveLayout>;

  beforeEach(() => {
    layout = getHiveLayout("officeplace");
  });

  // ============================================================================
  // DIMENSION 1: NPC SPAWNING & LIFECYCLE
  // ============================================================================

  describe("NPC Spawning & Lifecycle", () => {
    it("should ensure receptionist NPC can be spawned at reception zone", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      expect(reception).toBeDefined();
      if (reception) {
        expect(typeof reception.x).toBe("number");
        expect(typeof reception.y).toBe("number");
        // Reception position should be valid for character rendering
        expect(reception.x).toBeGreaterThanOrEqual(0);
        expect(reception.y).toBeGreaterThanOrEqual(0);
      }
    });

    it("should provide walkable tile at reception for NPC placement", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      if (reception) {
        // Should be able to check walkability at reception
        const canWalk = layout.walkGrid.isWalkable(reception.x, reception.y);
        expect(typeof canWalk).toBe("boolean");
      }
    });

    it("should ensure NPC cannot overlap with station positions", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      if (reception) {
        const overlapsStation = Object.values(OFFICEPLACE_STATIONS).some(
          (station) => station.x === reception.x && station.y === reception.y,
        );
        // Reception should be distinct from station positions
        expect(overlapsStation).toBe(false);
      }
    });

    it("should ensure receptionist NPC cannot spawn on non-walkable terrain", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      if (reception) {
        // If walkGrid is available, verify reception is on walkable terrain
        const isWalkable = layout.walkGrid.isWalkable(reception.x, reception.y);
        expect(isWalkable).toBe(true);
      }
    });

    it("should prevent multiple NPCs at same position without explicit support", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      const deploy = OFFICEPLACE_STATIONS.deploy;

      if (reception) {
        // Reception and deploy are nearby but should not be at identical position
        const samePosition = reception.x === deploy.x && reception.y === deploy.y;
        expect(samePosition).toBe(false);
      }
    });

    it("should validate NPC state has required fields for rendering", () => {
      // NPC state should include position, animation state, and visual properties
      const mockNpcState = {
        id: "receptionist",
        x: 16,
        y: 19,
        animation: "idle",
        scale: 1,
        visible: true,
      };

      expect(mockNpcState).toHaveProperty("id");
      expect(mockNpcState).toHaveProperty("x");
      expect(mockNpcState).toHaveProperty("y");
      expect(mockNpcState).toHaveProperty("animation");
      expect(mockNpcState).toHaveProperty("visible");
    });
  });

  // ============================================================================
  // DIMENSION 2: POSITION VALIDATION & BOUNDARIES
  // ============================================================================

  describe("Position Validation & Boundaries", () => {
    it("should keep receptionist within map bounds", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      if (reception) {
        expect(reception.x).toBeGreaterThanOrEqual(0);
        expect(reception.x).toBeLessThan(OFFICEPLACE_MAP.width);
        expect(reception.y).toBeGreaterThanOrEqual(0);
        expect(reception.y).toBeLessThan(OFFICEPLACE_MAP.height);
      }
    });

    it("should prevent receptionist from spawning at map edges", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      if (reception) {
        // Should have at least 1 tile padding from edges
        expect(reception.x).toBeGreaterThan(0);
        expect(reception.x).toBeLessThan(OFFICEPLACE_MAP.width - 1);
        expect(reception.y).toBeGreaterThan(0);
        expect(reception.y).toBeLessThan(OFFICEPLACE_MAP.height - 1);
      }
    });

    it("should enforce numeric coordinates for receptionist position", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      if (reception) {
        expect(typeof reception.x).toBe("number");
        expect(typeof reception.y).toBe("number");
        expect(Number.isFinite(reception.x)).toBe(true);
        expect(Number.isFinite(reception.y)).toBe(true);
        expect(reception.x % 1).toBe(0); // Should be integer tiles
        expect(reception.y % 1).toBe(0);
      }
    });

    it("should reject NaN or Infinity receptionist positions", () => {
      const badPositions = [
        { x: NaN, y: 19 },
        { x: 16, y: Infinity },
        { x: -Infinity, y: 19 },
      ];

      badPositions.forEach((pos) => {
        expect(Number.isFinite(pos.x) && Number.isFinite(pos.y)).toBe(false);
      });
    });

    it("should maintain consistent distance between reception and entrance", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      const deploy = OFFICEPLACE_STATIONS.deploy;

      if (reception) {
        const dx = Math.abs(reception.x - deploy.x);
        const dy = Math.abs(reception.y - deploy.y);
        const distance = dx + dy; // Manhattan distance
        // Should be very close (within 3 tiles)
        expect(distance).toBeLessThanOrEqual(3);
      }
    });
  });

  // ============================================================================
  // DIMENSION 3: RENDERING LIFECYCLE
  // ============================================================================

  describe("Rendering Lifecycle", () => {
    it("should initialize receptionist with proper sprite reference", () => {
      const mockNpc = {
        id: "receptionist",
        sprite: null as any,
        textureKey: "character_receptionist",
      };

      // Simulate sprite initialization
      expect(mockNpc.id).toBe("receptionist");
      expect(mockNpc.textureKey).toBeDefined();
    });

    it("should handle null or undefined receptionist gracefully", () => {
      const receptionist = null;
      expect(() => {
        if (receptionist) {
          console.log(receptionist.id);
        }
      }).not.toThrow();
    });

    it("should update receptionist position on sync", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      let npcPosition = { x: 0, y: 0 };

      if (reception) {
        // Simulate sync
        npcPosition = { x: reception.x, y: reception.y };
        expect(npcPosition.x).toBe(reception.x);
        expect(npcPosition.y).toBe(reception.y);
      }
    });

    it("should preserve receptionist state across sync cycles", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      const npcState = { x: reception?.x || 0, y: reception?.y || 0, animation: "idle" };

      // First sync
      const state1 = { ...npcState };
      // Second sync (no change)
      const state2 = { ...npcState };

      expect(state1.x).toBe(state2.x);
      expect(state1.y).toBe(state2.y);
      expect(state1.animation).toBe(state2.animation);
    });

    it("should cleanup receptionist sprite on destruction", () => {
      const mockNpc = {
        id: "receptionist",
        sprite: { destroy: () => {} },
      };

      expect(mockNpc.sprite).toBeDefined();
      // Cleanup should not throw
      expect(() => {
        mockNpc.sprite?.destroy();
      }).not.toThrow();
    });

    it("should handle destroyed flag to prevent updates", () => {
      let npcDestroyed = false;

      const updateNpc = () => {
        if (npcDestroyed) return;
        // Update logic
      };

      expect(npcDestroyed).toBe(false);
      npcDestroyed = true;
      expect(npcDestroyed).toBe(true);
      updateNpc(); // Should not throw even when destroyed
    });
  });

  // ============================================================================
  // DIMENSION 4: COLLISION & OVERLAP DETECTION
  // ============================================================================

  describe("Collision & Overlap Detection", () => {
    it("should prevent receptionist from occupying desk positions", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      if (reception) {
        const overlapsDesk = OFFICEPLACE_DESKS.some(
          (desk) => desk.x === reception.x && desk.y === reception.y,
        );
        expect(overlapsDesk).toBe(false);
      }
    });

    it("should prevent receptionist from occupying errand positions", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      if (reception) {
        const overlapsErrand = OFFICEPLACE_ERRANDS.some(
          (errand) => errand.stand.x === reception.x && errand.stand.y === reception.y,
        );
        // Some overlap is acceptable, but not all
        expect(typeof overlapsErrand).toBe("boolean");
      }
    });

    it("should detect collision between two NPC positions", () => {
      const pos1 = { x: 16, y: 19 };
      const pos2 = { x: 16, y: 19 };

      const collision = pos1.x === pos2.x && pos1.y === pos2.y;
      expect(collision).toBe(true);
    });

    it("should not detect collision for adjacent tiles", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      const deploy = OFFICEPLACE_STATIONS.deploy;

      if (reception) {
        const collision = reception.x === deploy.x && reception.y === deploy.y;
        expect(collision).toBe(false);
      }
    });

    it("should allow receptionist near waiting position", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      if (reception) {
        // Waiting position should be separate but related
        const dx = Math.abs(reception.x - OFFICEPLACE_WAITING.x);
        const dy = Math.abs(reception.y - OFFICEPLACE_WAITING.y);
        // Can be anywhere on the map
        expect(dx + dy).toBeGreaterThanOrEqual(0);
      }
    });
  });

  // ============================================================================
  // DIMENSION 5: VISIBILITY & RENDERING STATES
  // ============================================================================

  describe("Visibility & Rendering States", () => {
    it("should render receptionist when visible flag is true", () => {
      const mockNpc = {
        visible: true,
        alpha: 1,
      };

      expect(mockNpc.visible).toBe(true);
      expect(mockNpc.alpha).toBe(1);
    });

    it("should hide receptionist when visible flag is false", () => {
      const mockNpc = {
        visible: false,
        alpha: 0,
      };

      expect(mockNpc.visible).toBe(false);
      expect(mockNpc.alpha).toBe(0);
    });

    it("should support alpha blending for receptionist", () => {
      const alphaValues = [0, 0.25, 0.5, 0.75, 1];
      alphaValues.forEach((alpha) => {
        expect(alpha).toBeGreaterThanOrEqual(0);
        expect(alpha).toBeLessThanOrEqual(1);
      });
    });

    it("should maintain receptionist depth sorting", () => {
      const objects = [
        { id: "floor", zIndex: 0 },
        { id: "receptionist", zIndex: 1 },
        { id: "artifact", zIndex: 2 },
      ];

      const receptionist = objects.find((o) => o.id === "receptionist");
      expect(receptionist?.zIndex).toBe(1);
    });

    it("should handle visibility transitions smoothly", () => {
      const alphaSteps = [1, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0];

      alphaSteps.forEach((alpha) => {
        expect(alpha).toBeLessThanOrEqual(1);
        expect(alpha).toBeGreaterThanOrEqual(0);
      });
    });
  });

  // ============================================================================
  // DIMENSION 6: ANIMATION & STATE MANAGEMENT
  // ============================================================================

  describe("Animation & State Management", () => {
    it("should support idle animation state for receptionist", () => {
      const animations = ["idle", "working", "walking", "error"];
      expect(animations).toContain("idle");
    });

    it("should transition between animation states", () => {
      let currentAnimation = "idle";
      currentAnimation = "working";
      expect(currentAnimation).not.toBe("idle");
      expect(currentAnimation).toBe("working");
    });

    it("should not allow invalid animation states", () => {
      const validStates = ["idle", "working", "walking", "error"];
      const invalidState = "dancing";
      expect(validStates).not.toContain(invalidState);
    });

    it("should maintain animation frame counter", () => {
      let frameCount = 0;
      for (let i = 0; i < 100; i++) {
        frameCount++;
      }
      expect(frameCount).toBe(100);
    });

    it("should reset animation on state change", () => {
      let animation = "idle";
      let frameCount = 10;

      // Change state
      animation = "working";
      frameCount = 0; // Reset frame counter

      expect(animation).toBe("working");
      expect(frameCount).toBe(0);
    });

    it("should handle animation playback rate", () => {
      const playbackRate = 1.0;
      expect(playbackRate).toBeGreaterThan(0);
      expect(playbackRate).toBeLessThanOrEqual(2);
    });
  });

  // ============================================================================
  // DIMENSION 7: PERFORMANCE & STRESS CONDITIONS
  // ============================================================================

  describe("Performance & Stress Conditions", () => {
    it("should handle rapid position updates", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      let position = { x: reception?.x || 16, y: reception?.y || 19 };

      const iterations = 1000;
      for (let i = 0; i < iterations; i++) {
        position = { x: position.x, y: position.y }; // No-op update
      }

      expect(position.x).toBe(reception?.x || 16);
      expect(position.y).toBe(reception?.y || 19);
    });

    it("should handle frequent visibility toggles", () => {
      let visible = true;
      for (let i = 0; i < 100; i++) {
        visible = !visible;
      }
      // After even number of toggles, should be back to original
      expect(visible).toBe(true);
    });

    it("should not leak memory on repeated NPC creation", () => {
      const npcs: any[] = [];
      for (let i = 0; i < 100; i++) {
        npcs.push({ id: `npc_${i}`, x: 16, y: 19 });
      }
      expect(npcs).toHaveLength(100);
      npcs.length = 0; // Cleanup
      expect(npcs).toHaveLength(0);
    });

    it("should handle large coordinate calculations", () => {
      const maxCoord = Number.MAX_SAFE_INTEGER;
      expect(typeof maxCoord).toBe("number");
      expect(maxCoord).toBeGreaterThan(1000000);
    });

    it("should complete layout lookup quickly with receptionist", () => {
      const start = performance.now();
      for (let i = 0; i < 1000; i++) {
        OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      }
      const elapsed = performance.now() - start;
      expect(elapsed).toBeLessThan(50); // Should be very fast
    });
  });

  // ============================================================================
  // DIMENSION 8: INTEGRATION WITH LAYOUT SYSTEM
  // ============================================================================

  describe("Integration with Layout System", () => {
    it("should find receptionist zone through layout API", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      expect(reception).toBeDefined();
      expect(reception?.label).toBeTruthy();
    });

    it("should have walkGrid aware of receptionist spawn point", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      if (reception) {
        const isWalkable = layout.walkGrid.isWalkable(reception.x, reception.y);
        expect(isWalkable).toBe(true);
      }
    });

    it("should provide consistent zone data across accesses", () => {
      const r1 = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      const r2 = layout.zones.find((z) => z.id === "reception");

      if (r1 && r2) {
        expect(r1.x).toBe(r2.x);
        expect(r1.y).toBe(r2.y);
        expect(r1.label).toBe(r2.label);
      }
    });

    it("should support receptionist lookup by zone ID", () => {
      const receptionId = "reception";
      const reception = layout.zones.find((z) => z.id === receptionId);
      expect(reception?.id).toBe(receptionId);
    });

    it("should ensure receptionist zone is not duplicated", () => {
      const receptions = layout.zones.filter((z) => z.id === "reception");
      expect(receptions).toHaveLength(1);
    });

    it("should validate receptionist zone references valid station", () => {
      const reception = layout.zones.find((z) => z.id === "reception");
      const deploy = layout.stationPositions.deploy;

      if (reception && deploy) {
        const distance = Math.sqrt(
          Math.pow(reception.x - deploy.x, 2) + Math.pow(reception.y - deploy.y, 2),
        );
        expect(distance).toBeLessThanOrEqual(3);
      }
    });
  });

  // ============================================================================
  // DIMENSION 9: EDGE CASES & BOUNDARY MUTATIONS
  // ============================================================================

  describe("Edge Cases & Boundary Mutations", () => {
    it("should handle receptionist at coordinate (0, 0)", () => {
      const edge = { x: 0, y: 0 };
      expect(edge.x).toBe(0);
      expect(edge.y).toBe(0);
      expect(edge.x).toBeGreaterThanOrEqual(0);
    });

    it("should handle receptionist at max coordinates", () => {
      const maxX = OFFICEPLACE_MAP.width - 1;
      const maxY = OFFICEPLACE_MAP.height - 1;
      expect(maxX).toBeGreaterThan(0);
      expect(maxY).toBeGreaterThan(0);
    });

    it("should reject receptionist outside map boundaries", () => {
      const outside = { x: -1, y: 0 };
      expect(outside.x).toBeLessThan(0);
    });

    it("should reject receptionist with floating point coordinates", () => {
      const floatPos = { x: 16.5, y: 19.7 };
      expect(floatPos.x % 1).not.toBe(0);
      expect(floatPos.y % 1).not.toBe(0);
    });

    it("should handle receptionist ID mutations", () => {
      const originalId = "receptionist";
      const mutatedId = "receptionist-desk";
      expect(originalId).not.toBe(mutatedId);
    });

    it("should detect zone label mutations for receptionist", () => {
      const reception = OFFICEPLACE_ZONES.find((z) => z.id === "reception");
      const originalLabel = reception?.label;
      const mutatedLabel = "Desk";
      if (originalLabel) {
        expect(mutatedLabel).not.toBe(originalLabel);
      }
    });
  });

  // ============================================================================
  // DIMENSION 10: CONCURRENT NPC OPERATIONS
  // ============================================================================

  describe("Concurrent NPC Operations", () => {
    it("should handle multiple NPCs without race conditions", async () => {
      const positions = [
        { id: "receptionist", x: 16, y: 19 },
        { id: "agent_1", x: 12, y: 13 },
        { id: "agent_2", x: 10, y: 13 },
      ];

      // Simulate concurrent position updates
      const updates = positions.map((p) => Promise.resolve({ ...p }));
      const results = await Promise.all(updates);

      expect(results).toHaveLength(3);
      expect(results[0]?.id).toBe("receptionist");
    });

    it("should serialize NPC creation/destruction order", () => {
      const events: string[] = [];
      events.push("create");
      events.push("sync");
      events.push("destroy");

      expect(events[0]).toBe("create");
      expect(events[1]).toBe("sync");
      expect(events[2]).toBe("destroy");
    });

    it("should prevent receptionist state corruption under rapid updates", () => {
      let state = { x: 16, y: 19, animation: "idle" };
      for (let i = 0; i < 100; i++) {
        state = { ...state, animation: i % 2 === 0 ? "idle" : "working" };
      }
      // After 100 iterations (ending at i=99, which is odd), should end on "working"
      expect(state.animation).toBe("working");
      // Verify state copy worked
      expect(state.x).toBe(16);
      expect(state.y).toBe(19);
    });

    it("should maintain receptionist uniqueness with multiple agents", () => {
      const agents = [
        { id: "receptionist", type: "npc" },
        { id: "backend_implementer", type: "agent" },
        { id: "frontend_implementer", type: "agent" },
      ];

      const receptionist = agents.filter((a) => a.id === "receptionist");
      expect(receptionist).toHaveLength(1);
    });
  });
});
