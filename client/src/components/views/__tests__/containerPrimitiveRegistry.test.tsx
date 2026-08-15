/**
 * AC1, AC5, and the registry's unknown-id fallback.
 *
 * The module surface these tests pin (nothing client-side existed for
 * containers when they were written — sibling 434's `lib/viewsApi.ts` is on its
 * own branch):
 *
 *   - `views/primitives/types.ts` — `SettingsField`, `PrimitiveEntry<TSettings>`,
 *     `RegisteredPrimitive`.
 *   - `views/primitives/definePrimitive.tsx` — `definePrimitive<TSettings>()`.
 *     This is the whole answer to AC1's "no `as any`": the entry is generic over
 *     its *parsed* settings type, and `definePrimitive` closes over that generic
 *     — wrapping the component so it receives `parseSettings(raw)` — which is
 *     what lets the registry's value type erase `TSettings` without a cast. The
 *     chat registry at `components/chat/primitives/registry.tsx` casts every
 *     entry `as Renderer`; that is the shape AC1 forbids, and a source scan
 *     below says so.
 *   - `views/primitives/registry.tsx` — `CONTAINER_PRIMITIVES`, `getPrimitive`,
 *     `ContainerPrimitiveHost`.
 *   - `views/primitives/excludedPanels.ts` — `EXCLUDED_PANELS`, AC5's record.
 *
 * Wire conventions, from 433: `settings` is an open map whose keys are
 * snake_case (`primitive_id`, `workspace_slug`, `ticket_id`, `url`), and
 * `primitive_id` lives *inside* `settings`. Parsed settings are camelCase.
 */

import fs from "fs";
import path from "path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";

import { EXCLUDED_PANELS } from "../primitives/excludedPanels";
import {
  CONTAINER_PRIMITIVES,
  ContainerPrimitiveHost,
  getPrimitive,
} from "../primitives/registry";

/**
 * Any suite that mounts a *registered* primitive has to supply what a real view
 * supplies, or it passes and fails on which entry happens to sit at index 0 of
 * the registry — jsdom has no ResizeObserver, and react-query has no client.
 * The environment is stubbed rather than the primitives mocked away.
 */
beforeEach(() => {
  jest
    .spyOn(globalThis, "requestAnimationFrame")
    .mockImplementation((cb) => ((cb as FrameRequestCallback)(0), 1));
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = class {
    static readonly OPEN = 1;
    readyState = 0;
    onopen: (() => void) | null = null;
    onmessage: (() => void) | null = null;
    onclose: (() => void) | null = null;
    onerror: (() => void) | null = null;
    send(): void {}
    close(): void {}
  };
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe(): void {}
    disconnect(): void {}
    unobserve(): void {}
  };
});

afterEach(() => jest.restoreAllMocks());

function renderHost(containerId: string, settings: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContainerPrimitiveHost containerId={containerId} settings={settings} />
    </QueryClientProvider>,
  );
}

function readSource(relativePath: string): string {
  return fs.readFileSync(path.resolve(__dirname, "../../../..", relativePath), "utf8");
}

function viewsSourceFiles(): string[] {
  const root = path.resolve(__dirname, "..");
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== "__tests__") walk(full);
      } else if (/\.(tsx?|css)$/.test(entry.name)) {
        out.push(full);
      }
    }
  };
  walk(root);
  return out;
}

describe("AC1 — a typed registry maps ids to display metadata, a settings schema, and a component", () => {
  it("registers at least the three container kinds the ticket names", () => {
    // ContainerKind (server enum, 433): terminal | panel | web_embed. The
    // registry's own ids are a finer vocabulary that this ticket owns.
    const kinds = new Set(CONTAINER_PRIMITIVES.map((p) => p.containerKind));
    expect(kinds).toEqual(new Set(["terminal", "panel", "web_embed"]));
  });

  it("gives every entry the metadata a picker needs, keyed by a unique id", () => {
    expect(CONTAINER_PRIMITIVES.length).toBeGreaterThanOrEqual(3);
    const ids = CONTAINER_PRIMITIVES.map((p) => p.id);
    expect(new Set(ids).size).toBe(ids.length);

    for (const entry of CONTAINER_PRIMITIVES) {
      expect(entry.id).toMatch(/^[a-z][a-z0-9_]*$/);
      expect(entry.displayName.length).toBeGreaterThan(0);
      expect(entry.icon.length).toBeGreaterThan(0);
      expect(entry.category.length).toBeGreaterThan(0);
      expect(typeof entry.Component).toBe("function");
      expect(typeof entry.parseSettings).toBe("function");
      expect(Array.isArray(entry.settingsFields)).toBe(true);
    }
  });

  it("describes each settings field well enough to generate an input for it", () => {
    for (const entry of CONTAINER_PRIMITIVES) {
      for (const field of entry.settingsFields) {
        expect(field.key).toMatch(/^[a-z][a-z0-9_]*$/);
        expect(field.label.length).toBeGreaterThan(0);
        expect(["string", "number", "boolean"]).toContain(field.kind);
        expect(field).toHaveProperty("default");
      }
    }
  });

  it("resolves an id through getPrimitive and refuses one it does not know", () => {
    for (const entry of CONTAINER_PRIMITIVES) {
      expect(getPrimitive(entry.id)).toBe(entry);
    }
    expect(getPrimitive("not_a_primitive")).toBeUndefined();
    // A lookup keyed directly on the stored string must not reach a prototype
    // member — the store holds attacker-influencable text.
    expect(getPrimitive("constructor")).toBeUndefined();
    expect(getPrimitive("__proto__")).toBeUndefined();
    expect(getPrimitive("toString")).toBeUndefined();
  });

  it("keeps the registry free of the escape hatches AC1 exists to forbid", () => {
    // Non-behavioural criterion, so it is checked the way this repo already
    // checks non-behavioural criteria (see lib/__tests__/ledgerStatusSharedUsage).
    for (const file of viewsSourceFiles()) {
      const source = fs.readFileSync(file, "utf8");
      expect({ file, hit: /\bas\s+any\b/.test(source) }).toEqual({ file, hit: false });
      expect({ file, hit: /@ts-ignore|@ts-expect-error|@ts-nocheck/.test(source) }).toEqual({
        file,
        hit: false,
      });
      // `as unknown as X` is `as any` with extra steps, and is how a
      // non-generic registry entry would be smuggled in.
      expect({ file, hit: /\bas\s+unknown\s+as\b/.test(source) }).toEqual({ file, hit: false });
    }
  });

  it("erases the settings generic through definePrimitive rather than a cast at the entry", () => {
    // `definePrimitive` is generic over the entry's *parsed* settings type and
    // closes over it, which is the only way the registry's value type can drop
    // `TSettings` without a cast. Whether the calls live in registry.tsx or in
    // each primitive's own module is the implementer's choice; what is fixed is
    // that they exist and that nothing is asserted into the registry's type.
    expect(readSource("src/components/views/primitives/definePrimitive.tsx")).toMatch(
      /function\s+definePrimitive\s*</,
    );
    const calls = viewsSourceFiles()
      .filter((file) => !file.endsWith("definePrimitive.tsx"))
      .reduce(
        (total, file) =>
          total + (fs.readFileSync(file, "utf8").match(/\bdefinePrimitive\s*</g) ?? []).length,
        0,
      );
    expect(calls).toBeGreaterThanOrEqual(CONTAINER_PRIMITIVES.length);

    for (const file of viewsSourceFiles()) {
      const source = fs.readFileSync(file, "utf8");
      const cast = /\bas\s+(RegisteredPrimitive|PrimitiveEntry)\b/.test(source);
      expect({ file, cast }).toEqual({ file, cast: false });
    }
  });
});

describe("AC1 / bug 444 — parseSettings narrows, and a missing or nulled value falls back to its default", () => {
  it("returns the same parsed settings for an empty map and an all-null map", () => {
    // 444: a non-finite number inside `settings` is coerced to `null` on the
    // way in, so a stored setting can arrive as null where a number is
    // declared. That must read as "use the default", never as a crash and never
    // as a null leaking into the component's props.
    for (const entry of CONTAINER_PRIMITIVES) {
      const allNull: Record<string, unknown> = {};
      for (const field of entry.settingsFields) allNull[field.key] = null;

      const fromEmpty = entry.parseSettings({});
      const fromNulls = entry.parseSettings(allNull);
      expect(fromNulls).toEqual(fromEmpty);
    }
  });

  it("falls back to the declared default for every individually nulled field", () => {
    for (const entry of CONTAINER_PRIMITIVES) {
      for (const field of entry.settingsFields) {
        const parsed = entry.parseSettings({ [field.key]: null }) as Record<string, unknown>;
        for (const value of Object.values(parsed)) {
          expect(value).not.toBeNull();
        }
        // A number field is the exact 444 case; assert the default lands.
        if (field.kind === "number") {
          expect(Object.values(parsed)).toContain(field.default);
        }
      }
    }
  });

  it("survives values of the wrong type without throwing", () => {
    const junk: Record<string, unknown>[] = [
      {},
      { primitive_id: "terminal" },
      { url: 42, workspace_slug: [], ticket_id: {} },
      { url: undefined, workspace_slug: null, ticket_id: NaN },
      // A settings map that arrived as JSON can carry a `__proto__` key.
      JSON.parse('{"__proto__": {"url": "https://polluted.example"}}'),
    ];
    for (const entry of CONTAINER_PRIMITIVES) {
      for (const raw of junk) {
        expect(() => entry.parseSettings(raw)).not.toThrow();
      }
    }
  });

  it("does not pass undeclared raw keys through to the component", () => {
    // `parseSettings` is the narrowing step, and an implementation that spreads
    // the raw map and patches a couple of fields on top would satisfy every
    // assertion above while handing the component unvalidated snake_case the
    // registry's types say is not there. `primitive_id` is the routing key and
    // is not a setting of any primitive either.
    // Whether the parsed keys are camelCase or keep the wire's snake_case is
    // the implementer's choice and is not asserted; what is asserted is that a
    // key nobody declared does not survive the parse.
    for (const entry of CONTAINER_PRIMITIVES) {
      const parsed = entry.parseSettings({
        primitive_id: entry.id,
        unexpected_key: "smuggled",
      }) as Record<string, unknown>;
      expect(Object.keys(parsed)).not.toContain("unexpected_key");
      expect(Object.keys(parsed)).not.toContain("primitive_id");
      expect(Object.values(parsed)).not.toContain("smuggled");
    }
  });
});

describe("unknown primitive_id falls back to a placeholder pane", () => {
  it("renders the fallback instead of throwing or resolving the stored string", () => {
    const { container } = renderHost("c1", { primitive_id: "totally_unknown" });
    const host = container.querySelector("[data-container-id='c1']");
    expect(host).not.toBeNull();
    expect(host).toHaveAttribute("data-primitive-unknown", "true");
  });

  it("treats a missing primitive_id the same way", () => {
    const { container } = renderHost("c1", {});
    expect(container.querySelector("[data-primitive-unknown='true']")).not.toBeNull();
  });

  it("treats a primitive_id that is not a string the same way", () => {
    // `settings` is an open map the server does not validate, so the stored
    // value is whatever was written — a number, an object, or an array whose
    // `toString()` happens to name a real primitive.
    for (const primitiveId of [42, null, {}, ["terminal"], true]) {
      const { container } = renderHost("c1", { primitive_id: primitiveId });
      expect(container.querySelector("[data-primitive-unknown='true']")).not.toBeNull();
    }
  });

  it("marks every registered primitive as known, whichever one it is", () => {
    // Indexing one entry made this pass or fail on registration order; every
    // entry has to survive being mounted through the host.
    for (const known of CONTAINER_PRIMITIVES) {
      const { container } = renderHost("c1", { primitive_id: known.id });
      const host = container.querySelector("[data-container-id='c1']");
      expect({ id: known.id, found: host !== null }).toEqual({ id: known.id, found: true });
      expect(host).toHaveAttribute("data-primitive-id", known.id);
      expect(host).not.toHaveAttribute("data-primitive-unknown", "true");
    }
  });

  it("never turns the stored id into a dynamic import", () => {
    const source = readSource("src/components/views/primitives/registry.tsx");
    expect(source).not.toMatch(/import\s*\(/);
    expect(source).not.toMatch(/\brequire\s*\(/);
  });
});

describe("AC5 — panels ruled out are recorded with a reason and absent from the registry", () => {
  /**
   * Ruled out at triage. Each either reads page-level state directly
   * (`uiStore` / `QueueStatusContext`), or needs a caller-held domain object a
   * container's JSON `settings` cannot produce.
   */
  const RULED_OUT = [
    "LogsPanel",
    "ApprovalInboxPanel",
    "CopilotDock",
    "HiveSimulationPanel",
    "FailedRunsPanel",
    "TicketDiffReviewPanel",
    "InlineCodeDiffReview",
    "QueueDashboard",
  ];

  it("records every ruled-out panel with a non-empty reason", () => {
    const recorded = new Map(EXCLUDED_PANELS.map((p) => [p.component, p.reason]));
    for (const name of RULED_OUT) {
      expect(recorded.has(name)).toBe(true);
      expect((recorded.get(name) ?? "").trim().length).toBeGreaterThan(0);
    }
  });

  it("leaves them out of the registry rather than registering them broken", () => {
    const registered = new Set(CONTAINER_PRIMITIVES.map((p) => p.displayName));
    for (const name of RULED_OUT) {
      expect(registered.has(name)).toBe(false);
    }
    // Absent means not imported either: a half-decoupled panel wired in behind
    // a different display name would still show up here.
    for (const file of viewsSourceFiles()) {
      if (file.endsWith("excludedPanels.ts")) continue;
      const source = fs.readFileSync(file, "utf8");
      for (const name of RULED_OUT) {
        // Named, default, namespace, or bare-module — a panel pulled in under a
        // local alias still lands here, because the module path names it.
        const imported =
          new RegExp(`import[^;]*\\b${name}\\b[^;]*from`).test(source) ||
          new RegExp(`from\\s*["'][^"']*/${name}["']`).test(source);
        expect({ file, name, imported }).toEqual({ file, name, imported: false });
      }
    }
  });
});
