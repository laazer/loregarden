# Test Design: Add smart import button to import modal UI (33-add-smart-import-button-to-import-modal-ui)

> **Stage:** `test_design` · **Agent:** spec · **Run:** run_266b03
> **Deliverable:** Deterministic, implementation-ready test specification. This document defines the
> component contract and the exact test cases the Test Designer / Implementer must realize. It does
> **not** contain test code.
> **Parent capability:** 32-import-modal-ui-enhancement · **Sibling (downstream):** 34-route-smart-import-selection-to-studio-with-prev

---

## Requirement: SMART-IMPORT-MODE-SELECTOR

### 1. Spec Summary

- **Description:** `ImportTicketsModal` (`client/src/components/ImportTicketsModal.tsx`) must present two
  mutually-exclusive import modes to the user: **Regular import** (existing behavior) and
  **Smart import** (new). Exactly one mode is selected at any time. The selection is surfaced to the
  parent via the existing continue action so the downstream routing ticket (34) can consume it.
  Ticket 33 is **UI-only**: it renders the selector, tracks the selected mode, labels/tooltips the
  difference, and passes the mode outward. It does **not** implement the Studio routing/preview flag
  (that is ticket 34).

- **Constraints:**
  1. Existing regular-import behavior (file selection via `ImportTicketFileExplorer`, selected-file
     list, Cancel, and Continue) must remain unchanged and fully functional in `"regular"` mode.
  2. Reuse existing modal styling primitives (`modal-*`, `btn-primary`, `btn-secondary`, `state-label`)
     — no bespoke colors/spacing. The mode selector must use existing token-based classes.
  3. No new runtime dependencies. React + existing test stack (`vitest`, `@testing-library/react`,
     `@testing-library/user-event`, `@testing-library/jest-dom`) only.
  4. Additive, backward-compatible prop/callback changes only (see contract below).

- **Assumptions (explicit — flagged for confirmation in §4):**
  - A1. **Presentation:** the selector is a two-option control at the top of `modal-body`, rendered as a
    radio group (`role="radiogroup"`) with two selectable option buttons. Default selected mode is
    `"regular"`.
  - A2. **Selection semantics:** selecting a mode is non-destructive — it does not clear the current
    file selection and does not close the modal.
  - A3. **Handoff:** `onContinue` is extended to `onContinue(filePaths: string[], mode: ImportMode)`.
    The second argument is additive; existing callers that ignore it are unaffected. `ImportMode = "regular" | "smart"`.
  - A4. **Labels:** option labels are exactly `"Regular import"` and `"Smart import"`. The smart option
    exposes clarifying help text via a `title` attribute **and** an `aria-describedby` reference so the
    difference from regular import is discoverable by pointer and by assistive tech.
  - A5. Smart mode does **not** change the Continue button's enable/disable rule (still requires ≥1 file).

- **Scope / Context:** `client/src/components/ImportTicketsModal.tsx` and its test file
  `client/src/components/__tests__/ImportTicketsModal.test.tsx` (to be created). Dashboard wiring of the
  new `mode` argument is validated only to the extent that `ImportTicketsModal`'s public contract is
  honored; end-to-end routing is out of scope (ticket 34).

### Component Contract (target of tests)

```
type ImportMode = "regular" | "smart";

interface ImportTicketsModalProps {
  open: boolean;
  workspaceSlug: string;
  initialBrowsePath?: string;
  isLoading: boolean;
  errorMessage?: string | null;
  onClose: () => void;
  onContinue: (filePaths: string[], mode: ImportMode) => void | Promise<void>; // mode ADDED (additive)
  initialMode?: ImportMode; // optional; defaults to "regular"
}
```

Required DOM contract (stable selectors for tests):
- Mode group: element with `role="radiogroup"` and `aria-label="Import mode"`.
- Two options, each `role="radio"`:
  - Regular: accessible name `"Regular import"`, `aria-checked` reflects selection.
  - Smart: accessible name `"Smart import"`, `aria-checked` reflects selection, `title` present and
    non-empty, `aria-describedby` points to a rendered element whose text explains the difference.
- Exactly one option has `aria-checked="true"` at all times.
- Options are reachable/operable by keyboard (Enter/Space and/or arrow keys per radiogroup semantics)
  and disabled when `isLoading` is true.

### 2. Acceptance Criteria (each independently verifiable)

Mapped to ticket AC: **(AC-a)** smart button renders & is selectable, **(AC-b)** labels/tooltips clarify
difference, **(AC-c)** design matches existing modal styles.

**Group R — Rendering (AC-a, AC-c)**
- R1. When `open={true}`, the modal renders a `radiogroup` labeled "Import mode" containing exactly two
  `radio` options named "Regular import" and "Smart import".
- R2. When `open={false}`, the modal (and therefore the mode selector) renders nothing
  (`queryByRole("dialog")` is null).
- R3. On first open with no `initialMode`, "Regular import" has `aria-checked="true"` and "Smart import"
  has `aria-checked="false"`.
- R4. When `initialMode="smart"`, "Smart import" is the checked option on open.
- R5. The mode options use existing button/token classes (assert class membership, e.g. option elements
  carry a shared mode-option class and the group sits inside `modal-body`); no inline color styles are
  applied to the options. (Guards AC-c against bespoke styling.)

**Group S — Selection behavior (AC-a)**
- S1. Clicking "Smart import" sets `aria-checked="true"` on it and `"false"` on "Regular import".
- S2. Clicking "Regular import" after S1 restores it as the checked option.
- S3. At every point, exactly one option is checked (assert count of `aria-checked="true"` === 1 across
  transitions).
- S4. Selecting a mode does NOT clear an existing file selection: with ≥1 file selected, switching modes
  keeps the "Selected (N)" list and its contents intact.
- S5. Selecting a mode does NOT call `onClose` and does NOT call `onContinue`.
- S6. Keyboard: focusing an option and pressing Space/Enter (and arrow-key navigation within the group)
  changes the selection consistently with pointer clicks.

**Group H — Handoff on Continue (AC-a, contract)**
- H1. With mode = "regular" (default) and one file `a.md` selected, clicking Continue calls
  `onContinue` once with `(["a.md"], "regular")`.
- H2. With mode switched to "smart" and files `a.md`, `b.md` selected, clicking Continue calls
  `onContinue` once with `(["a.md", "b.md"], "smart")` (order = selection order).
- H3. Continue remains disabled when zero files are selected regardless of mode; `onContinue` is not
  called (assert for both "regular" and "smart").
- H4. When `isLoading={true}`, both mode options are disabled (`aria-disabled`/`disabled`) and cannot
  change selection; Continue shows the loading label and is disabled.

**Group L — Labels & tooltips (AC-b)**
- L1. "Smart import" option has a non-empty `title` attribute whose text distinguishes it from regular
  import (asserted by substring, e.g. contains "smart" and references preview/Studio-style enrichment
  wording — see §3 R3 for wording risk).
- L2. "Smart import" option references an existing, rendered description element via `aria-describedby`,
  and that element's text is non-empty and distinct from the regular-import description/label.
- L3. Accessible names are exactly "Regular import" and "Smart import" (no icon-only ambiguity;
  `getByRole("radio", { name: /smart import/i })` resolves).

**Group C — Regression / backward compatibility**
- C1. Regular-import flow is unchanged: file explorer renders, toggling a file updates "Selected (N)",
  Cancel calls `onClose`, overlay click calls `onClose` when not loading.
- C2. Re-opening the modal (`open` false→true) resets the selected mode to `initialMode` (default
  "regular") and clears selected files, matching the existing `useEffect` reset behavior.
- C3. Existing callers passing an `onContinue` that reads only the first argument continue to work
  (mode arg is additive; verified by a caller that ignores arg 2).

### 3. Risk & Ambiguity Analysis

- **R1 — Presentation choice (radiogroup vs. two footer buttons vs. segmented toggle):** Tests are
  written against a `role="radiogroup"` contract (A1). If the design instead ships two independent
  footer submit buttons ("Continue" / "Smart import"), the selection-state tests (S-group) become
  invalid. **Mitigation:** confirm Q1 before implementation; the DOM contract in this doc is the source
  of truth for the Test Designer.
- **R2 — `onContinue` signature change couples 33 and 34:** Extending `onContinue` with a `mode` arg is
  the minimal handoff, but the actual consumption (routing to Studio + preview flag) is ticket 34.
  Risk: implementer over-reaches into routing. **Mitigation:** H-group asserts only that the correct
  `mode` is emitted; no routing assertions here.
- **R3 — Tooltip/label wording is unspecified:** ACs require the tooltip to "clarify the difference" but
  give no exact copy. Tests assert non-empty + distinctness + a "smart" substring rather than exact
  strings to avoid brittleness, but this leaves copy quality unverified. **Mitigation:** Q3.
- **R4 — Loading-state coverage of the new control:** existing modal disables inputs during
  `isLoading`; the new options must follow suit or a user could switch modes mid-request. H4 covers it.
- **R5 — Style-regression detection is weak in JSDOM:** JSDOM does not compute real styles, so AC-c can
  only be checked structurally (class membership, absence of inline color). True visual parity needs a
  manual/visual check. **Mitigation:** R5 asserts structural conventions; flag visual parity as a
  non-automated acceptance step.
- **R6 — Keyboard/radio semantics:** ARIA radiogroups have specific roving-tabindex/arrow-key
  expectations. If implemented with plain buttons + `aria-checked`, arrow-key nav may not work.
  S6 must accept either native-radio behavior or documented button-group keyboard handling — the test
  should assert the chosen, documented behavior (Q4).

### 4. Clarifying Questions

- **Q1 (blocking test shape):** Is the smart/regular selector a single `radiogroup` with one shared
  Continue action (this doc's assumption A1/A3), or two separate submit buttons in the footer? This
  determines whether S-group (selection-state) tests apply.
- **Q2:** Should `onContinue` carry the mode as a second argument (A3), or should the modal expose a
  separate `onSmartContinue`/`onModeChange` callback? Confirm the exact public API so H-group is correct.
- **Q3:** What is the approved tooltip/description copy for "Smart import" (and, if any, "Regular
  import")? Provide exact strings to allow precise assertions instead of substring matching.
- **Q4:** Required keyboard model for the selector — native `<input type="radio">` semantics (arrow keys
  move+select) or button group (Tab between, Space/Enter toggles)? This fixes S6.
- **Q5:** On re-open, should the modal remember the last-used mode, or always reset to `initialMode`
  (A2/C2 assume reset)? Confirm to finalize C2.

---

## Test Coverage Summary

- **Target test file:** `client/src/components/__tests__/ImportTicketsModal.test.tsx`
- **Total specified cases:** 24 (R:5, S:6, H:4, L:3, C:3, + 3 accessibility assertions embedded in R/L/S).
- **Fixtures:** minimal `SelectedImportFile` objects with `{ path, repo_path }`; `ImportTicketFileExplorer`
  mocked to emit deterministic `onToggleFile` events (mirror existing modal test patterns).
- **Determinism:** no timers, no network; user interaction via `@testing-library/user-event`.

## Non-automated acceptance (manual/visual)
- Visual parity of the mode selector with existing modal styles (AC-c) — verify tokens/spacing in the
  running app; JSDOM cannot assert computed styling (R5).

## Handoff
- **Next stage (`test_break`):** stress the contract above — attempt selections while `isLoading`,
  rapid mode toggling with partial file selections, `onContinue` double-invocation guards, and
  `aria-checked` invariants (exactly-one).
- **Downstream (ticket 34):** will consume the `mode` argument emitted by H1/H2; do not add routing
  assertions in this ticket's suite.
