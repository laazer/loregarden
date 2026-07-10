# Adversarial Test Analysis: Smart Import Button (Ticket 33)

## Test Breaker Agent Summary

**Run:** run_5d6fb3 · **Stage:** test_break · **Ticket:** 33-add-smart-import-button-to-import-modal-ui

This document logs the adversarial test suite designed to expose weaknesses, edge cases, and hidden assumptions in the smart import mode selector implementation.

---

## Gap Analysis: Original Test Suite vs. Adversarial Coverage

### Original Test Groups (R, S, H, L, C)

The test design spec and original test file provide solid coverage of:

- **Rendering (R1-R5):** Radio group structure, initial state, styling guards
- **Selection behavior (S1-S6):** Mode toggling, keyboard Space/Enter, file selection persistence
- **Handoff (H1-H4):** Mode argument emission, loading state blocking
- **Labels/tooltips (L1-L3):** Accessible naming, description elements
- **Regression (C1-C3):** File explorer functionality, re-open behavior, backward compatibility

### Gaps Exposed by Adversarial Testing (X7-X32)

The original suite has blind spots in critical production scenarios:

#### 1. **Concurrency & Double-Submit Prevention** (X7, X23)
- **Gap:** Original H-group tests click Continue once; production receives double-clicks or React strict mode double-renders
- **Risk:** Duplicate imports, database constraint violations, data corruption
- **Adversarial test:** X7 simulates rapid clicks; X23 tests Promise.all double-click race
- **Expected weakness:** Component may call onContinue twice without guarding

#### 2. **Async Handler Contract** (X14, X15)
- **Gap:** Test design says `onContinue: void | Promise<void>` but tests only verify sync calls
- **Risk:** Slow async operations (network uploads) break UI responsiveness or race with user interactions
- **Adversarial test:** X14 stubs a slow Promise; X15 tests rejection handling
- **Expected weakness:** Component may crash or not handle Promise rejections

#### 3. **Keyboard Navigation Completeness** (X12, X13, X25, X26)
- **Gap:** S6 only tests Space/Enter; misses arrow-key radiogroup contract
- **Risk:** Users on keyboard-only or assistive tech cannot navigate modes
- **Adversarial test:** X12/X13 test arrow-key wrap-around; X25/X26 test Tab/Enter
- **Expected weakness:** Arrow keys might not navigate; Tab order might be broken

#### 4. **Prop Change Reactions** (X10, X11, X16, X29)
- **Gap:** Original tests don't verify behavior when props change after mount (e.g., isLoading false→true)
- **Risk:** Mode state corrupts when parent re-renders; selection leaks between operations
- **Adversarial test:** X10 toggles isLoading; X11 adds/removes errorMessage; X16 changes workspaceSlug
- **Expected weakness:** Mode state may not survive dynamic prop changes

#### 5. **Semantic Correctness** (X17, X18, X19)
- **Gap:** Tests assume role=radio and proper aria-checked but don't assert them
- **Risk:** Assistive tech misinterprets control as buttons or other widgets
- **Adversarial test:** X17/X18/X19 explicitly check aria attributes
- **Expected weakness:** Implementation may use buttons with aria-checked instead of native radio

#### 6. **State Corruption Under Load** (X20, X21, X32)
- **Gap:** Rapid file toggling + mode switching not tested together
- **Risk:** UI state diverges from internal component state
- **Adversarial test:** X20 toggles files while mode is smart; X21 verifies button text updates
- **Expected weakness:** Mode state may reset or file count may not update correctly

#### 7. **Error Message Interactions** (X11, X22)
- **Gap:** No tests for errorMessage rendering or behavior with mode selector
- **Risk:** Error message placement could obscure mode selector; styling leaks
- **Adversarial test:** X11 adds/removes error; X22 tests null vs. empty string
- **Expected weakness:** Error message might overlap or break layout

#### 8. **Initial Arguments Stability** (X24, X30, X31)
- **Gap:** initialBrowsePath not tested against mode state; initialMode edge cases weak
- **Risk:** Mode selection influenced by unrelated props
- **Adversarial test:** X24 changes initialBrowsePath with mode="smart"; X30 tests missing initialMode
- **Expected weakness:** Props might cross-pollinate state

#### 9. **Visibility & DOM Placement** (X28)
- **Gap:** Tests don't verify mode selector is visible and doesn't obscure other controls
- **Risk:** Mode selector hidden or overlapping file explorer
- **Adversarial test:** X28 checks getBoundingClientRect().height > 0
- **Expected weakness:** CSS might hide the selector; z-index issues

#### 10. **Exact Argument Contract** (X8, X31)
- **Gap:** Tests verify onContinue call but don't validate argument count/order strictly
- **Risk:** Implementer swaps arguments or passes extra ones
- **Adversarial test:** X8 asserts exactly 2 arguments; X31 verifies order
- **Expected weakness:** Implementation may pass mode as first arg or emit 3+ args

---

## Test Breakdown by Category (Checklist Matrix)

### Null & Empty Values (X22, X30)
- `initialMode: undefined` → defaults to "regular" ✓
- `errorMessage: null` vs `""` → both render nothing ✓

### Boundary Conditions (X13, X21)
- Arrow key wrap-around (first → last, last → first) ✓
- Button text at 0, 1, 2 files (singular/plural) ✓

### Type & Structure Mutations (X8, X17, X19)
- `onContinue` receives (string[], string) not (string[]) alone ✓
- `aria-checked` is "true"/"false" not null/bool ✓
- `role="radio"` not `role="button"` ✓

### Invalid/Corrupt Inputs
- Rejecting async Promise (X15) ✓
- Slow Promise handling (X14) ✓

### Concurrency / Race Conditions (X7, X20, X23)
- Double-click on Continue (X7, X23) ✓
- Rapid file toggles + mode switch (X20) ✓

### Order Dependency (X12, X13)
- Arrow-key nav depends on current focus (X12) ✓
- Wrap-around is correct direction (X13) ✓

### Combinatorial Inputs (X10, X11, X16, X29)
- isLoading + mode + files (X10) ✓
- errorMessage + mode (X11) ✓
- workspaceSlug + mode (X16) ✓
- initialMode change on re-open (X29) ✓

### Stress / Load (X20, X21, X32)
- Many file toggles (X20) ✓
- Button text updates (X21) ✓
- Rapid aria-checked state transitions (X32) ✓

### Mutation Testing (X11, X16, X29)
- Prop changes don't reset mode state ✓
- initialMode prop is honored ✓

### Error Handling (X14, X15)
- Slow/rejecting onContinue (X14, X15) ✓
- Error state + mode interactions (X11) ✓

### Assumption Checks (X8, X17, X18, X28)
- Exact API contract (X8) ✓
- Semantic role assumptions (X17) ✓
- Accessibility labeling (X18) ✓
- DOM placement (X28) ✓

### Determinism Validation (X19, X32)
- aria-checked always "true" or "false" (X19) ✓
- Multiple toggles preserve invariant (X32) ✓

---

## Expected Failures on Current Implementation

The current `ImportTicketsModal` component **does not implement the smart import mode selector**. All tests in groups R, S, H, L, C, and X will fail until:

1. Mode state is added: `const [mode, setMode] = useState<"regular" | "smart">("regular")`
2. Radio group is rendered in modal-body
3. onContinue signature is updated: `onContinue(paths, mode)`
4. initialMode prop is added
5. Keyboard/accessibility semantics are implemented (roving tabindex, arrow keys)

### Test Status Baseline (FAILING)

| Group | Count | Status | Reason |
|-------|-------|--------|--------|
| R (Rendering) | 5 | FAIL | No radiogroup in DOM |
| S (Selection) | 6 | FAIL | No mode state or buttons |
| H (Handoff) | 4 | FAIL | onContinue signature unchanged |
| L (Labels) | 3 | FAIL | No radio options with titles |
| C (Regression) | 3 | PASS | File explorer unaffected |
| X (Adversarial) | 26 | MIXED | C1c stays green; others fail |

**Total: 47 test cases**
- Expected RED initially: 44 (R, S, H, L, X groups)
- Expected GREEN: 3 (C group regression tests)

---

## Implementation Risks Exposed

### High Severity

1. **Double-submit vulnerability (X7, X23):** Unguarded onContinue call allows duplicate imports
   - **Mitigation:** Disable Continue button immediately on click or wrap onContinue in debounce

2. **Async Promise handling (X14, X15):** Unhandled rejections or slow uploads break UI
   - **Mitigation:** Wrap onContinue in try-catch; disable Continue while pending

3. **Keyboard navigation broken (X12, X13):** Arrow keys don't navigate radiogroup
   - **Mitigation:** Use native `<input type="radio">` or implement roving tabindex + arrow handlers

### Medium Severity

4. **Prop change corruption (X10, X11, X16):** Mode state leaks across re-renders
   - **Mitigation:** Properly scope mode state; guard useEffect dependencies

5. **Semantic accessibility broken (X17, X18):** Assistive tech misreads control
   - **Mitigation:** Use proper `role="radio"` and `aria-labelledby`

6. **State race conditions (X20, X32):** Rapid interactions corrupt aria-checked invariant
   - **Mitigation:** Batch state updates; ensure always exactly one checked

### Low Severity

7. **Layout regression (X28):** Mode selector hidden or overlapping
   - **Mitigation:** Test visual layout; use correct CSS classes

8. **Type contract drift (X8, X31):** Arguments to onContinue mismatch caller expectations
   - **Mitigation:** Explicit type testing

---

## Recommendations for Implementer

1. **Start with semantic HTML:** Use native `<fieldset>` + `<input type="radio">` if possible
2. **Guard double-submit:** Disable Continue immediately on first click or debounce
3. **Handle Promises:** Wrap `void onContinue(...)` in try-catch and pending state
4. **Test keyboard:** Use real browser keyboard events, not just synthetic clicks
5. **Prop stability:** Use useEffect to reset mode only on `open` change, not every re-render
6. **Accessibility review:** Run ARIA linter and screen-reader tests

---

## Files

- **Test file:** `client/src/components/__tests__/ImportTicketsModal.test.tsx`
- **Implementation file:** `client/src/components/ImportTicketsModal.tsx` (pending)
- **Test design:** `client/src/components/__tests__/TEST_DESIGN_SmartImportButton.md`

---

## Test Coverage Metric

- **Test cases:** 47 (6 groups × 5-6 cases + 26 adversarial)
- **Coverage dimensions:** Rendering, selection, handoff, labels, regression, concurrency, async, keyboard, props, semantics, accessibility
- **Determinism:** 100% (no timers, no network mocks, controlled user events)

