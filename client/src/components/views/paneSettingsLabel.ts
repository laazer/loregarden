/**
 * What the pane header's settings control is called — the one spelling of it.
 *
 * Two modules need this string and they are on opposite sides of two rules:
 *
 *   - `PaneHeader` labels the control with it (`aria-label` *and* `title`, per
 *     434 — a bare glyph names nothing).
 *   - `primitives/Unconfigured` tells an unconfigured pane where to go, and it
 *     has to name the control that actually exists. Before 554 the panes said
 *     "…in its settings" and pointed at a surface nobody had built; a shared
 *     constant is what stops the copy drifting back into naming nothing.
 *
 * It is a module of its own rather than a line in `paneChrome`, which is where
 * the header's other pieces live, because `paneChrome` imports the primitive
 * registry: a constant there closes an import cycle
 * `registry → terminalPrimitive → Unconfigured → paneChrome → registry`. And it
 * is not in `primitives/` either — everything under that directory except the
 * registry and the shared vocabulary is off limits to the rest of the app, a
 * boundary `PrimitivePicker.test` enforces by resolving every import in `src/`.
 *
 * So: a leaf. It imports nothing, and both sides may import it.
 */

export const PANE_SETTINGS_LABEL = "Pane settings";
