/**
 * AC8 — "every registered primitive renders without overflow at a small
 * container size and reflows on resize."
 *
 * **This file does not measure anything, and cannot.** jsdom has no layout
 * engine: every element reports 0×0, `getComputedStyle` resolves nothing from a
 * stylesheet (jest maps `*.css` to `src/test/styleMock.ts`), and "overflows its
 * box" is therefore not a question that can be asked here. Claiming otherwise
 * would be a green test for a broken pane.
 *
 * What is checkable is the *structural cause* — the properties whose absence is
 * what makes a pane overflow a small container:
 *
 *   1. The host wrapper fills its container rather than sizing itself:
 *      `height: 100%`, `min-height: 0`, `min-width: 0`. `min-height: 0` is the
 *      one that actually matters: a flex child defaults to `min-height: auto`,
 *      which refuses to shrink below its content and is the mechanism behind
 *      almost every "it overflows when the pane is small" bug.
 *   2. Nothing in a primitive's own subtree pins its height in pixels or to the
 *      viewport. `QueueDashboard` — ruled out at triage — is the in-repo
 *      example: a 326px fixed rail, which is fine on a page and wrong in a
 *      120px pane.
 *   3. Nothing positions itself `fixed`, which escapes the container entirely.
 *
 * A CSS regression that only shows up in a browser (a stylesheet rule that
 * reintroduces a fixed height under a class this file cannot resolve) is not
 * caught here beyond the source scan, and is recorded as a gap on the ticket.
 * The reflow half of AC8 is exercised for real where it is observable — the
 * terminal's ResizeObserver, in `containerPrimitives.render.test.tsx`.
 */

import fs from "fs";
import path from "path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";

import { CONTAINER_PRIMITIVES, ContainerPrimitiveHost } from "../primitives/registry";

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

/**
 * A deliberately tiny container. jsdom will not lay it out — the point is that
 * every primitive mounts inside one without throwing, and that the host does
 * not respond by asserting a size of its own.
 */
function renderSmall(primitiveId: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <div style={{ width: 120, height: 90, display: "flex", minHeight: 0 }}>
        <ContainerPrimitiveHost containerId="tiny" settings={{ primitive_id: primitiveId }} />
      </div>
    </QueryClientProvider>,
  );
}

/**
 * Every `.css` file under `views/`, as text.
 *
 * jest maps `*.css` imports to a stub, so a stylesheet never reaches the
 * document and `getComputedStyle` cannot see it. Reading the file is the only
 * way a rule written in CSS counts for anything here — and without it this
 * whole file silently demands that the host be styled *inline*, which is a
 * constraint AC8 does not impose and this repo does not follow.
 */
function viewsCssRules(): { selector: string; declarations: Record<string, string> }[] {
  const root = path.resolve(__dirname, "..");
  const rules: { selector: string; declarations: Record<string, string> }[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== "__tests__") walk(full);
      } else if (entry.name.endsWith(".css")) {
        const source = fs.readFileSync(full, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
        for (const [, selector, body] of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
          const declarations: Record<string, string> = {};
          for (const declaration of body.split(";")) {
            const [property, ...rest] = declaration.split(":");
            if (rest.length === 0) continue;
            declarations[property.trim().toLowerCase()] = rest.join(":").trim().toLowerCase();
          }
          rules.push({ selector: selector.trim(), declarations });
        }
      }
    }
  };
  walk(root);
  return rules;
}

/** Inline styles plus whatever `views/` CSS says about this element's classes. */
function effectiveStyle(el: HTMLElement): Record<string, string> {
  const out: Record<string, string> = {};
  for (const cls of Array.from(el.classList)) {
    for (const rule of viewsCssRules()) {
      const matches = rule.selector
        .split(",")
        .some((part) => new RegExp(`\\.${cls}(?![\\w-])`).test(part));
      if (matches) Object.assign(out, rule.declarations);
    }
  }
  for (let i = 0; i < el.style.length; i += 1) {
    const property = el.style.item(i);
    out[property] = el.style.getPropertyValue(property).trim().toLowerCase();
  }
  return out;
}

function isZero(value: string | undefined): boolean {
  return value !== undefined && /^0(px|%)?$/.test(value);
}

describe("AC8 — every registered primitive mounts inside a small container", () => {
  it.each(CONTAINER_PRIMITIVES.map((p) => [p.id]))("%s mounts without throwing", (id) => {
    expect(() => renderSmall(id)).not.toThrow();
  });

  it.each(CONTAINER_PRIMITIVES.map((p) => [p.id]))(
    "%s fills its container instead of sizing itself",
    (id) => {
      const { container } = renderSmall(id);
      const host = container.querySelector<HTMLElement>("[data-container-id='tiny']");
      expect(host).not.toBeNull();
      const style = effectiveStyle(host as HTMLElement);

      // Three shapes fill a parent, and AC8 does not pick one: stretch to it,
      // grow into it as a flex child, or pin to its edges.
      const fills =
        style.height === "100%" ||
        /^(1|1 1 0|1 1 0%|1 1 auto)$/.test(style.flex ?? "") ||
        Number(style["flex-grow"] ?? 0) >= 1 ||
        (["absolute", "relative"].includes(style.position ?? "") && isZero(style.inset)) ||
        (style.position === "absolute" && isZero(style.top) && isZero(style.bottom));
      expect({ id, fills, style }).toEqual({ id, fills: true, style });

      // `min-height: 0` is the one with no alternative: a flex child defaults to
      // `min-height: auto` and simply refuses to shrink below its content.
      expect({ id, minHeight: style["min-height"] }).toEqual({
        id,
        minHeight: expect.stringMatching(/^0(px|%)?$/),
      });
      expect({ id, minWidth: style["min-width"] }).toEqual({
        id,
        minWidth: expect.stringMatching(/^0(px|%)?$/),
      });

      // An overflowing pane must scroll or clip; it must not push its neighbours.
      const clips = ["overflow", "overflow-y", "overflow-x"].some((property) =>
        ["auto", "hidden", "scroll"].includes(style[property] ?? ""),
      );
      expect({ id, clips, style }).toEqual({ id, clips: true, style });
    },
  );

  it.each(CONTAINER_PRIMITIVES.map((p) => [p.id]))(
    "%s pins no element's height in pixels or to the viewport",
    (id) => {
      const { container } = renderSmall(id);
      // Scoped to the host's own subtree — the 120x90 wrapper above it is this
      // test's stand-in for a pane, not the primitive's doing.
      const host = container.querySelector<HTMLElement>("[data-container-id='tiny']");
      for (const el of Array.from(host?.querySelectorAll<HTMLElement>("*") ?? [])) {
        const { height, minHeight, maxHeight, width, minWidth, position } = el.style;
        for (const value of [height, minHeight, width, minWidth]) {
          expect({ id, value, bad: /\d\s*(px|vh|vw|vmin|vmax)/.test(value) }).toEqual({
            id,
            value,
            bad: false,
          });
        }
        // max-height in a viewport unit is the same overflow bug wearing a hat.
        expect({ id, maxHeight, bad: /(vh|vw|vmin|vmax)/.test(maxHeight) }).toEqual({
          id,
          maxHeight,
          bad: false,
        });
        expect({ id, position }).not.toEqual({ id, position: "fixed" });
      }
    },
  );
});

describe("AC8 — the primitives' own sources assume no screen", () => {
  function viewsSources(): { file: string; source: string }[] {
    const root = path.resolve(__dirname, "..");
    const out: { file: string; source: string }[] = [];
    const walk = (dir: string) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          if (entry.name !== "__tests__") walk(full);
        } else if (/\.(tsx?|css)$/.test(entry.name)) {
          out.push({ file: full, source: fs.readFileSync(full, "utf8") });
        }
      }
    };
    walk(root);
    return out;
  }

  it("uses no viewport units", () => {
    for (const { file, source } of viewsSources()) {
      expect({ file, hit: /\d\s*(vh|vw|vmin|vmax)\b/.test(source) }).toEqual({ file, hit: false });
    }
  });

  it("positions nothing fixed", () => {
    for (const { file, source } of viewsSources()) {
      expect({ file, hit: /position:\s*fixed/.test(source) }).toEqual({ file, hit: false });
      expect({ file, hit: /position:\s*["']fixed["']/.test(source) }).toEqual({ file, hit: false });
    }
  });

  it("declares min-height: 0 in the same rule that declares a flex column", () => {
    // The flex-child default (`min-height: auto`) is the overflow mechanism; a
    // column that never says otherwise cannot shrink to a small pane. Checked
    // per rule, not per file: one unrelated `min-height: 0` elsewhere in the
    // stylesheet used to vouch for every column in it.
    for (const rule of viewsCssRules()) {
      if (!/^column/.test(rule.declarations["flex-direction"] ?? "")) continue;
      expect({
        selector: rule.selector,
        minHeight: rule.declarations["min-height"] ?? "(absent)",
      }).toEqual({ selector: rule.selector, minHeight: expect.stringMatching(/^0(px|%)?$/) });
    }
    // Inline-styled columns in TSX are the same bug; the block-scoped form is
    // not expressible as a regex, so this stays a file-level check for them.
    for (const { file, source } of viewsSources()) {
      if (!/\.css$/.test(file) && /flexDirection:\s*["']column/.test(source)) {
        expect({ file, declaresMinHeight: /minHeight:\s*["']?0/.test(source) }).toEqual({
          file,
          declaresMinHeight: true,
        });
      }
    }
  });

  it("pins no height in pixels large enough to break a small pane", () => {
    // The in-repo example AC8 names: QueueDashboard's 326px rail, fine on a
    // page and wrong in a 120x90 container. Small pixel values (a 28px toolbar
    // row) are ordinary and are not what overflows anything, so the bar is set
    // at the height of the pane this file mounts.
    for (const rule of viewsCssRules()) {
      for (const property of ["height", "min-height"]) {
        const value = rule.declarations[property];
        const pixels = value?.match(/^(\d+(?:\.\d+)?)px$/);
        const tooTall = pixels !== null && pixels !== undefined && Number(pixels[1]) >= 200;
        expect({ selector: rule.selector, property, value, tooTall }).toEqual({
          selector: rule.selector,
          property,
          value,
          tooTall: false,
        });
      }
    }
  });
});
