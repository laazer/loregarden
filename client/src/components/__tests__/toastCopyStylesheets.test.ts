/**
 * S3.6, S8 — the stylesheet is the subject under test.
 *
 * jsdom loads none of the app's CSS, so none of this is observable from a
 * render: every element in it reports transparent, at 0px, and `max-height:
 * 5.8em` hides nothing. Two different criteria therefore have to be read off
 * the files themselves.
 *
 * `.toast__message`'s clamp is FENCED (lg-external-harness-954 AC13/AC21). The
 * route to the untruncated text is the clipboard, not a wider toast, so the
 * assertion here is that the rule is still exactly what it was.
 *
 * `.copy-value-btn`'s base rules reach a component today only because
 * `TicketDetailsModal.tsx` happens to have been loaded — a latent bug that
 * renders an unstyled button wherever that import is absent, which is exactly
 * what putting the control in a toast would have hit.
 */

import fs from "fs";
import path from "path";

const SRC = path.resolve(__dirname, "../..");
const INDEX_CSS = path.join(SRC, "index.css");
const TOAST_CSS = path.join(SRC, "components/ToastHost.css");
const TICKET_MODAL_CSS = path.join(SRC, "components/TicketDetailsModal.css");
const TOAST_HOST_TSX = path.join(SRC, "components/ToastHost.tsx");

function read(file: string): string {
  return fs.readFileSync(file, "utf8");
}

/** Comments stripped, for assertions that are about declarations only. */
function withoutComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** The declarations of one rule, trimmed, in source order. */
function declarations(css: string, selector: string): string[] | null {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = withoutComments(css).match(new RegExp(`(^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`, "m"));
  if (!match) return null;
  return match[2]
    .split(";")
    .map((declaration) => declaration.trim())
    .filter(Boolean);
}

describe("S3.6 — the 4-line clamp is not widened", () => {
  const FENCED_RULE = `.toast__message {
  margin-top: 3px;
  font-size: 12px;
  line-height: 1.45;
  color: var(--txm);
  /* Server detail can be a stack-sized string; keep the toast a toast. */
  max-height: 5.8em;
  overflow: hidden;
  overflow-wrap: anywhere;
}`;

  it("keeps the rule and its comment byte-identical", () => {
    expect(read(TOAST_CSS)).toContain(FENCED_RULE);
  });

  it("states it in one place, so a second rule cannot quietly override it", () => {
    const occurrences = withoutComments(read(TOAST_CSS)).match(/\.toast__message\s*\{/g) ?? [];
    expect(occurrences).toHaveLength(1);
  });

  it("builds none of the affordances that were decided against", () => {
    // UX2: no `title` tooltip on the message (mouse-only, so it answers nobody
    // the criterion is about), no on-screen reveal, no expand, no new modal.
    // The full text is already in the accessibility tree — `overflow: hidden`
    // clips pixels, it does not remove text.
    const tsx = read(TOAST_HOST_TSX);
    const messageElement = tsx.match(/<div className="toast__message"[^>]*>/);
    expect(messageElement).not.toBeNull();
    expect(messageElement![0]).not.toContain("title=");
    expect(tsx).not.toMatch(/role="dialog"/);
  });
});

describe("S8.1/S8.2 — the copy button's base rules live where every surface can reach them", () => {
  const BASE = [
    "width: 22px",
    "height: 22px",
    "flex: none",
    "display: inline-flex",
    "align-items: center",
    "justify-content: center",
    "padding: 0",
    "border: 1px solid transparent",
    "border-radius: 6px",
    "background: transparent",
    "color: var(--txl)",
    "cursor: pointer",
  ];

  it("S8.1 — index.css carries the base block, declarations unchanged", () => {
    expect(declarations(read(INDEX_CSS), ".copy-value-btn")).toEqual(BASE);
  });

  it("S8.1 — index.css carries the interaction and copied states too", () => {
    const css = read(INDEX_CSS);
    expect(
      declarations(css, ".copy-value-btn:hover:not(:disabled),\n.copy-value-btn:focus-visible"),
    ).toEqual(["background: var(--bg3)", "border-color: var(--bd)", "color: var(--tx)"]);
    expect(declarations(css, ".copy-value-btn.is-copied")).toEqual(["color: var(--grn)"]);
  });

  it("S8.2 — TicketDetailsModal.css keeps exactly one .copy-value-btn rule, its own override", () => {
    const css = withoutComments(read(TICKET_MODAL_CSS));
    const selectors = (css.match(/[^{}]*\.copy-value-btn[^{}]*\{/g) ?? []).map((s) =>
      s.replace(/\{$/, "").trim(),
    );
    expect(selectors).toEqual([".ticket-modal-title-row .copy-value-btn"]);
    expect(declarations(css, ".ticket-modal-title-row .copy-value-btn")).toEqual(["margin-top: 6px"]);
  });

  it("S8.3 — introduces no colour literal in the copy button's rules", () => {
    // Scoped to the rules this change writes. `.toast`'s own box-shadow
    // carries an rgba() that predates it, and the theme gate is diff-scoped
    // for the same reason.
    for (const file of [INDEX_CSS, TOAST_CSS]) {
      const css = withoutComments(read(file));
      for (const [, body] of css.matchAll(/\.copy-value-btn[^{}]*\{([^}]*)\}/g)) {
        expect(body).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(body).not.toMatch(/\b(?:rgb|rgba|hsl|hsla|oklch|lab)\(/);
      }
    }
  });
});

describe("S8.4/S8.5 — where toast-specific styling lives, and no new motion", () => {
  it("S8.4 — any toast-scoped copy rule is in ToastHost.css, not in index.css", () => {
    const toastScoped = (css: string) =>
      (withoutComments(css).match(/\.toast[^{}]*\.copy-value-btn[^{}]*\{/g) ?? []).length;
    expect(toastScoped(read(INDEX_CSS))).toBe(0);
    // Zero is also a legitimate answer: the shared rules may already position
    // it. What is forbidden is reaching into `.copy-value-btn`'s own
    // declarations from the toast's side.
    expect(declarations(read(TOAST_CSS), ".copy-value-btn")).toBeNull();
  });

  it("S8.5 — ToastHost.css adds no transition, keyframe or hardcoded duration", () => {
    const css = withoutComments(read(TOAST_CSS));
    expect(css).not.toMatch(/transition\s*:/);
    expect(css).not.toMatch(/@keyframes/);
    expect(css).not.toMatch(/animation\s*:/);
    expect(css).not.toMatch(/\b\d+m?s\b/);
  });

  it("S8.5 — the toast keeps the motion tokens it already had", () => {
    const tsx = read(TOAST_HOST_TSX);
    expect(tsx).toContain("duration: DURATION.med");
    expect(tsx).toContain("duration: DURATION.fast");
    expect(tsx).toContain("ease: EASE_OUT");
  });

  it("S8.3 — adds no raw button to the toast; the primitives are the controls", () => {
    const tsx = read(TOAST_HOST_TSX);
    expect(tsx).not.toMatch(/<button\b/);
    expect(tsx).toContain("CopyValueButton");
    expect(tsx).toContain("IconCloseButton");
  });
});
