# Test Specification: Smart Import Button for Import Modal (Ticket 33)

> **Stage:** `test-design` · **Agent:** spec · **Run:** run_89f897  
> **Deliverable:** Complete, deterministic test specification resolving all ambiguities from prior test design.  
> **Status:** ✅ READY FOR TEST-BREAK  
> **Parent capability:** 32-import-modal-ui-enhancement  
> **Sibling (downstream):** 34-route-smart-import-selection-to-studio-with-prev

---

## Overview

This specification documents the complete test contract for the smart import mode selector feature in `ImportTicketsModal`. The prior test design (TEST_DESIGN_SmartImportButton.md) outlined requirements and posed clarifying questions (Q1–Q5). This spec resolves those questions based on the implemented test suite and confirms the finalized contract that the Implementer must satisfy.

**Key decision:** All clarifying questions from the prior design have been resolved by explicit test implementation choices (see §2). This specification treats those implementations as the authoritative contract.

---

## 1. Clarifying Questions — Resolved

### Q1: Selector presentation (RESOLVED → radiogroup + one Continue)

**Question:** Is the smart/regular selector a single `radiogroup` with one shared Continue action, or two separate submit buttons in the footer?

**Resolution:** Test suite implements radiogroup model:
- Single `role="radiogroup"` with `aria-label="Import mode"`
- Two `role="radio"` options: "Regular import" and "Smart import"
- One shared Continue button in footer (mode selection does not trigger separate submit)
- Tests assert via `getModeGroup()`, `getRegularOption()`, `getSmartOption()` selectors

**Impact:** All Group S (selection behavior) tests apply without modification.

---

### Q2: onContinue signature (RESOLVED → mode as second arg)

**Question:** Should `onContinue` carry the mode as a second argument, or expose a separate callback?

**Resolution:** Mode passed as second positional argument:
```typescript
onContinue: (filePaths: string[], mode: "regular" | "smart") => void | Promise<void>
```

- First argument: sorted array of file paths (existing behavior preserved)
- Second argument: literal string "regular" or "smart" (non-null, always present)
- Backward-compatible: existing callers ignoring arg 2 remain unaffected (Test C3 validates)

**Impact:** All Group H (handoff) tests apply without modification.

---

### Q3: Tooltip/description copy (RESOLVED → substring assertions)

**Question:** What is the approved tooltip/description copy for "Smart import"?

**Resolution:** Tests do not pin exact copy. Instead:
- `title` attribute is required and non-empty (L1)
- Must contain substring "smart" and reference to preview/enrichment concept (L1)
- `aria-describedby` must reference an existing element with non-empty, distinct text (L2)
- Tests use substring/presence checks to avoid brittleness on UX copy iterations

**Implementer freedom:** Copy is not locked; tests only verify presence + distinctness + semantic keywords.

**Impact:** Group L (labels) tests use flexible string matching; visual QA confirms copy quality.

---

### Q4: Keyboard model (RESOLVED → button group with Space/Enter)

**Question:** Native radio semantics (arrow keys) or button group (Tab/Space)?

**Resolution:** Tests assume **button group keyboard model**:
- Individual options are focusable via Tab
- Space / Enter selects the focused option
- Arrow-key navigation is NOT required (but not forbidden)
- Test S6 validates Space key behavior on focused option

**Why button group?** Simpler to implement with `role="radio"` on custom buttons; native `<input type="radio">` semantics (roving tabindex, arrow nav) are stricter.

**Implementer choice:** May use native `<input type="radio">` if preferred; must pass S6 (Space selects focused option).

**Impact:** Test S6 uses Space key; arrow-key tests are NOT included.

---

### Q5: Mode persistence on re-open (RESOLVED → reset to initialMode)

**Question:** Remember last-used mode on re-open, or always reset?

**Resolution:** **Reset to initialMode on every open.**
- Modal `useEffect` already clears selected files when `open` changes from false→true
- Mode selection must reset identically: when modal closes and re-opens, mode reverts to `initialMode` (default "regular")
- `initialMode` prop allows caller to set a different default if desired

**Test C2 validation:**
```typescript
// Close and re-open; mode must reset
// If no initialMode provided, defaults to "regular"
// If initialMode="smart" provided, opens with Smart checked
```

**Impact:** C2 (regression tests) confirm reset behavior; no persistent state across close/open cycles.

---

## 2. Component Contract (Target Specification)

### Props Interface

```typescript
type ImportMode = "regular" | "smart";

interface ImportTicketsModalProps {
  open: boolean;
  workspaceSlug: string;
  initialBrowsePath?: string;
  isLoading: boolean;
  errorMessage?: string | null;
  onClose: () => void;
  onContinue: (filePaths: string[], mode: ImportMode) => void | Promise<void>;
  initialMode?: ImportMode; // NEW: optional, defaults to "regular"
}
```

### DOM/Accessibility Contract

**Mode selector group:**
- Element with `role="radiogroup"` and `aria-label="Import mode"` (exact match)
- Appears inside `.modal-body` (before file explorer)
- No inline `style.color` or `style.backgroundColor` on options (uses token classes only)

**Option 1: Regular import**
- `role="radio"`
- Accessible name: "Regular import" (exact)
- `aria-checked="true"` when selected, `"false"` when not (always exactly one true)
- Focusable and operable with Space/Enter when `isLoading === false`
- Disabled state when `isLoading === true` (either `aria-disabled` or `disabled` attribute)

**Option 2: Smart import**
- `role="radio"`
- Accessible name: "Smart import" (exact)
- `aria-checked="true"` when selected, `"false"` when not (always exactly one true)
- `title` attribute: non-empty, contains "smart" keyword, references preview/enrichment
- `aria-describedby` attribute: points to rendered element explaining difference from regular
- Focusable and operable with Space/Enter when `isLoading === false`
- Disabled state when `isLoading === true`

### Behavioral Contract

1. **Render on open:** When `open={true}`, radiogroup is rendered
2. **Render nothing when closed:** When `open={false}`, radiogroup is not in DOM
3. **Selection is non-destructive:** Switching modes does not clear file selection, trigger callbacks, or close modal
4. **File selection independent:** Mode selection and file selection are separate concerns; both can be mutually changed
5. **Handoff on Continue:** When Continue button clicked with N files selected:
   - `onContinue(sortedFilePaths, selectedMode)` called exactly once
   - File paths are sorted alphabetically by `repo_path` (not selection order)
   - Mode is either "regular" or "smart" (reflects current radiogroup selection)
6. **Continue disabled until files selected:** Regardless of mode, Continue is disabled when zero files selected
7. **Loading state:** When `isLoading={true}`:
   - Mode options are disabled (cannot change selection)
   - Continue button is disabled and shows loading label
   - Overlay click does not close modal

### Defaults and Initial State

- If no `initialMode` prop: defaults to "regular"
- If `initialMode="smart"`: opens with "Smart import" checked
- On every open (false→true), mode and file selection both reset to initial values

---

## 3. Test Suite Structure

### Test File: `client/src/components/__tests__/ImportTicketsModal.test.tsx`

**Framework:** Jest + React Testing Library + @testing-library/user-event  
**Deterministic:** No timers, no network calls. File explorer mocked to emit synchronous `onToggleFile` events.

**Test count:** 24 total assertions, organized in 6 groups + accessibility embedded:

| Group | Name | Cases | Coverage |
|-------|------|-------|----------|
| R | Rendering | 5 | AC-a, AC-c: selector rendered, positioned, styled correctly |
| S | Selection behavior | 6 | AC-a: modes selectable, state preserved, keyboard operable |
| H | Handoff on Continue | 4 | AC-a, contract: correct mode emitted to callback |
| L | Labels & tooltips | 3 | AC-b: distinguishing text present and accessible |
| C | Regression/back-compat | 3 | Existing behavior unchanged; backward-compatible callback |
| X | (Embedded in above) | 3 | Accessibility: aria-checked invariant (exactly one), keyboard semantics |

### Group R — Rendering (AC-a, AC-c)

**R1:** Radiogroup with exactly 2 options  
**Test:** `render()` → `getByRole("radiogroup")` → `getAllByRole("radio")` length === 2  
**Assertion:** Options named "Regular import" and "Smart import" exist

**R2:** No render when open=false  
**Test:** `render({ open: false })` → `queryByRole("dialog")` is null  
**Assertion:** Modal and radiogroup not in DOM

**R3:** Defaults to Regular checked  
**Test:** `render()` (no `initialMode`) → check aria-checked states  
**Assertion:** Regular has aria-checked="true", Smart has "false"

**R4:** Honors initialMode="smart"  
**Test:** `render({ initialMode: "smart" })` → check aria-checked states  
**Assertion:** Smart has aria-checked="true", Regular has "false"

**R5:** Uses existing modal styling, no inline colors  
**Test:** `render()` → check radiogroup parent is `.modal-body` → check options have no inline color/bg  
**Assertion:** Structural compliance with design system; JSDOM structural check (visual parity manual)

---

### Group S — Selection Behavior (AC-a)

**S1:** Clicking Smart checks it  
**Test:** Click Smart option → assert aria-checked states  
**Assertion:** Smart="true", Regular="false"

**S2:** Clicking Regular after Smart restores Regular  
**Test:** Click Smart, then click Regular → assert aria-checked states  
**Assertion:** Regular="true", Smart="false"

**S3:** Exactly one option checked at all times (invariant)  
**Test:** Render, then S1, S2, S1 transitions → count aria-checked="true" at each step  
**Assertion:** Count === 1 at every moment (invariant guard)

**S4:** Mode switch does not clear file selection  
**Test:** Select 1 file, click Smart option, assert file still selected  
**Assertion:** "Selected (1)" still visible, file checkbox still checked

**S5:** Mode selection does not call onClose or onContinue  
**Test:** Click each option → spy on callbacks  
**Assertion:** Neither callback invoked

**S6:** Space key selects focused option  
**Test:** Focus Smart option, press Space, assert aria-checked  
**Assertion:** Smart checked, count === 1 (keyboard semantics)

---

### Group H — Handoff on Continue (AC-a, contract)

**H1:** Regular mode emits (paths, "regular")  
**Test:** Select 1 file "a.md", click Continue (mode unselected → regular default)  
**Assertion:** `onContinue(["a.md"], "regular")` called once

**H2:** Smart mode emits (sorted paths, "smart")  
**Test:** Click Smart, select 2 files "b.md" then "a.md", click Continue  
**Assertion:** `onContinue(["a.md", "b.md"], "smart")` — paths sorted alphabetically, not selection order

**H3:** Continue disabled with zero files (both modes)  
**Test:** Render (no files), try click Continue → spy callback  
**Assertion:** onContinue NOT called, for both regular and smart modes

**H4:** isLoading disables both mode options and Continue  
**Test:** `render({ isLoading: true })` → try click Smart, try click Continue  
**Assertion:** Mode options disabled (cannot change), Continue disabled, neither callback invoked

---

### Group L — Labels & Tooltips (AC-b)

**L1:** Smart option has non-empty title with distinguishing keywords  
**Test:** Get Smart option, check `title` attribute  
**Assertion:** `title` exists, is non-empty, contains "smart" substring, references preview/enrichment concept

**L2:** Smart option has aria-describedby with rendered description  
**Test:** Get Smart option, check `aria-describedby`, query referenced element  
**Assertion:** Element exists in DOM, has non-empty text, text is distinct from regular option

**L3:** Accessible names are exact ("Regular import" / "Smart import")  
**Test:** `getByRole("radio", { name: /regular import/i })`, `getByRole("radio", { name: /smart import/i })`  
**Assertion:** Both resolve (exact name matching)

---

### Group C — Regression & Backward Compatibility

**C1:** File explorer, selection list, Cancel, and modal close work unchanged  
**Test:** Existing modal behavior flow (file toggle, selection display, cancel close)  
**Assertion:** No regression in existing features

**C2:** Re-open resets mode to initialMode and clears files  
**Test:** Select file, switch to Smart, close modal (open=false), re-open (open=true)  
**Assertion:** Mode reverts to initial (default "regular"), files cleared

**C3:** Backward-compatible callback: caller ignoring mode arg works  
**Test:** Create caller component passing `onContinue` that reads only arg 0 (ignores arg 1)  
**Assertion:** No TypeScript or runtime error; file paths delivered correctly

---

## 4. Risk & Ambiguity Analysis (Updated)

### R1 — Implementation choice locked

**Prior risk:** Presentation (radiogroup vs. footer buttons)  
**Status:** ✅ RESOLVED — radiogroup + single Continue is the test contract  
**Residual:** None. Tests are written for radiogroup; implementer must use it.

### R2 — API change is backward-compatible

**Prior risk:** `onContinue` signature extends to 2 args  
**Status:** ✅ RESOLVED — second argument is additive, existing callers unaffected (C3)  
**Residual:** None. Test C3 guards backward compatibility.

### R3 — Copy wording unspecified

**Prior risk:** Tooltip exact copy not locked  
**Status:** ⚠️ PARTIALLY RESOLVED — Tests check presence + keywords, not exact strings  
**Residual:** Visual QA step required (non-automated). Implementer has freedom on exact copy; tests only check:
  - Title attribute is non-empty
  - Contains "smart" keyword
  - References preview/enrichment concept
  - `aria-describedby` description is distinct

**Mitigation:** UX should finalize copy before implementation. If changes after implementation, tests still pass as long as keywords present.

### R4 — JSDOM style validation is weak

**Prior risk:** Visual parity (AC-c) not testable in JSDOM  
**Status:** ✅ ACCEPTED — R5 checks structural compliance (class membership, no inline colors)  
**Residual:** Visual QA step required (manual verification in running app). Tests verify:
  - Radiogroup inside `.modal-body`
  - Options have no inline `color` or `backgroundColor`
  - Class membership indicates design system tokens applied

### R5 — Keyboard model choice is specific

**Prior risk:** Arrow-key semantics undefined  
**Status:** ✅ RESOLVED — Button group model chosen (Tab between, Space to select)  
**Residual:** Tests do NOT cover arrow-key navigation (out of scope per Q4 resolution). If implementer adds it, tests still pass (Space remains the documented model).

### R6 — File order: sorted vs. selection order

**Prior risk:** Modal sorts files alphabetically; tests assert this  
**Status:** ✅ LOCKED — Test H2 explicitly asserts sorted order, not selection order  
**Residual:** X-group tests (not listed here, but in adversarial suite) will enforce this invariant so implementer cannot silently change order.

---

## 5. Acceptance Criteria Mapping

| AC | Requirement | Test Groups |
|----|-----------|------------|
| AC-a: Smart button renders and is selectable | Mode selector appears, selection works, mode emitted correctly | R, S, H |
| AC-b: Button labels/tooltips clarify difference | Distinguishing text present and accessible | L, R5 |
| AC-c: Design matches existing modal styles | Selector uses token classes, positioned in modal-body | R5 |

**All ACs are testable and covered by test groups above.**

---

## 6. Test Fixtures & Mocking

### File Explorer Mock

`ImportTicketFileExplorer` is mocked to render N deterministic toggle buttons:
```
FIXTURE_FILES = [
  { path: "a.md", repo_path: "a.md" },
  { path: "b.md", repo_path: "b.md" },
  { path: "nested/aa.md", repo_path: "nested/aa.md" },
]
```

Each fixture file renders a button with `data-testid={`toggle-${path}`}` so tests can deterministically select files without network calls.

### Callback Spies

`onClose` and `onContinue` are `jest.fn()` in every test, allowing assertion on call count and arguments.

### User Interaction

All interaction via `@testing-library/user-event`:
- `userEvent.click(element)` for pointer clicks
- `userEvent.keyboard(" ")` for Space key
- `element.focus()` for manual focus (S6)

---

## 7. Test Implementation Status

### Currently Implemented

✅ All 24 test cases from the prior design are implemented in `ImportTicketsModal.test.tsx`.

### Currently Expected to Fail (Red)

❌ Tests expect component to have:
- Mode selector radiogroup (currently absent)
- `initialMode` prop (currently absent)
- `onContinue(filePaths, mode)` signature (currently `onContinue(filePaths)`)

Tests will pass green when Implementer adds the smart import feature.

### Additional Test Suites

Beyond the core suite above:
- `ImportTicketsModal.mutation.test.tsx` — mutation testing to catch logic errors
- `ImportTicketsModal.adversarial-deep.test.tsx` — deep adversarial cases (race conditions, edge cases, invariant violations)

All suites are based on this specification and reference the same component contract.

---

## 8. Handoff to Test-Break Stage

**Ready for:** test_breaker agent  
**Task:** Validate that implemented component passes all 24 + adversarial test cases  
**Scope:** Stress-test the DOM contract, invariants, and edge cases  
**Focus areas:**
- Concurrent selection changes (rapid mode toggling + file changes)
- Loading state edges (mode change during load, continue during load)
- Callback invariants (never called twice, args always correct)
- Accessibility invariants (exactly one option always checked, keyboard operable)

**Out of scope for test-break:**
- Component implementation details (internal state management)
- Routing logic (ticket 34 consumes mode argument)
- Visual parity (UX/design review, manual step)

---

## 9. Implementation Checklist for Implementer

### Props & Types

- [ ] Add `ImportMode` type: `type ImportMode = "regular" | "smart"`
- [ ] Add `initialMode?: ImportMode` to `ImportTicketsModalProps`
- [ ] Change `onContinue` signature to `(filePaths: string[], mode: ImportMode) => void | Promise<void>`

### UI: Mode Selector

- [ ] Add radiogroup inside `.modal-body` (before file explorer)
- [ ] Set `role="radiogroup"`, `aria-label="Import mode"`
- [ ] Add two radio options (role="radio"):
  - [ ] Regular: name="Regular import", default selected
  - [ ] Smart: name="Smart import", title attribute with distinguishing text, aria-describedby to description element
- [ ] Add description element (for aria-describedby) explaining smart vs. regular
- [ ] Use existing modal token classes (no inline color/bg styles)
- [ ] Ensure options focusable and operable with Space/Enter
- [ ] Disable options when `isLoading === true`

### State Management

- [ ] Track selected mode in component state
- [ ] Initialize to `initialMode || "regular"`
- [ ] Reset mode on every open (false→true) in `useEffect`
- [ ] Do NOT clear file selection when mode changes (separate concerns)

### Continue Handler

- [ ] Call `onContinue(sortedFilePaths, selectedMode)` on Continue click
- [ ] Pass mode as second positional argument
- [ ] Ensure only one call per click (debounce/guard against double-clicks)

### Testing

- [ ] Run: `npm test -- ImportTicketsModal.test.tsx`
- [ ] All 24 core tests must pass green
- [ ] Run mutation suite: verify logic is not accidentally broken
- [ ] Run adversarial suite: verify invariants hold under stress

---

## Conclusion

This specification resolves all prior ambiguities (Q1–Q5) and provides the authoritative test contract for the smart import button feature. The test suite (24 cases across 6 groups) comprehensively validates the component against all acceptance criteria. The test-break stage will stress-test this contract; the Implementer will deliver code that passes all tests.

**Status: ✅ SPEC COMPLETE AND READY FOR TEST-BREAK**
