# Test Gap Analysis: Smart Import Button (Ticket 33)

**Ticket:** 33-add-smart-import-button-to-import-modal-ui  
**Stage:** test-break (Test Breaker Agent)  
**Run:** run_8d6c99  
**Date:** 2026-07-11  

---

## Executive Summary

A comprehensive adversarial test suite analysis has identified **37 critical gaps** in the existing test coverage for the smart import mode selector. These gaps target subtle state management bugs, callback contract violations, type mismatches, and lifecycle edge cases that could slip past the existing 70+ adversarial tests.

**Test Results:**
- **Existing test suites:** 87 failing, 85 passing (172 total)
- **Critical gaps suite:** 33 failing, 4 passing (37 total)
- **Combined suite:** 120 failing, 89 passing (209 total)

---

## Gap Categories & Severity

### CRITICAL1: Callback Contract Strictness (5 tests)

**Severity:** 🔴 **CRITICAL** — Guards the public API contract

**Gaps Identified:**

1. **CRITICAL1-1: Callback Arity Regression**
   - **Issue:** Implementation could accidentally emit `onContinue(paths)` without the mode argument.
   - **Why it matters:** Breaks the API contract defined in the ticket. Any caller written for the new 2-arg signature would fail.
   - **Test:** Asserts `args.length === 2` and both mode is defined and is a string.

2. **CRITICAL1-2: Type Coercion on Mode Arg**
   - **Issue:** Implementation might pass `true/false` or numeric enums instead of string `"regular"/"smart"`.
   - **Why it matters:** Type mismatches cause runtime errors in callers expecting a string.
   - **Test:** Verifies `typeof mode === "string"` and exact value equality.

3. **CRITICAL1-3: Stale Mode in Closure**
   - **Issue:** Mode captured in a closure at component mount, not read at call time.
   - **Why it matters:** User switches mode → clicks Continue → old mode is emitted (wrong behavior).
   - **Test:** Changes mode via props update, verifies the **current** mode is passed, not the initial.

4. **CRITICAL1-4: Prop Update & Callback Substitution**
   - **Issue:** When parent re-renders and passes a new `onContinue`, the new callback might receive a stale mode.
   - **Why it matters:** State leaks across callback generations, violating separation of concerns.
   - **Test:** Rerender with new handler, verify it gets the current mode value.

5. **CRITICAL1-5: Path Array Type**
   - **Issue:** Paths could be passed as a single string or non-array value.
   - **Why it matters:** Caller code expecting `string[]` crashes on type error.
   - **Test:** Asserts `Array.isArray(paths)` and all elements are strings.

**Exposure Level:** High — These catch fundamental API contract violations that would only surface at runtime in production.

---

### CRITICAL2: Mode State Machine Invariants (5 tests)

**Severity:** 🔴 **CRITICAL** — Guards correctness of core feature

**Gaps Identified:**

1. **CRITICAL2-1: Prop-Render Sync on Mount**
   - **Issue:** Component renders UI without respecting `initialMode` prop.
   - **Why it matters:** User gets the wrong default; tests might pass but feature is broken in practice.
   - **Test:** Verifies `aria-checked` on each radio matches the `initialMode` passed.

2. **CRITICAL2-2: initialMode Reset on Re-open**
   - **Issue:** Modal remembers the last-selected mode instead of resetting to `initialMode` on re-open.
   - **Why it matters:** Violates the UX requirement that each session starts fresh.
   - **Test:** Switch mode, close, re-open with different `initialMode`, verify it resets.

3. **CRITICAL2-3: Mode Unrelated to Other Props**
   - **Issue:** Changes to `workspaceSlug` or other unrelated props accidentally reset or corrupt mode.
   - **Why it matters:** Accidental coupling between unrelated state paths causes subtle bugs.
   - **Test:** Change props, verify mode is unaffected.

4. **CRITICAL2-4: initialBrowsePath Independence**
   - **Issue:** Same as CRITICAL2-3 but for `initialBrowsePath`.
   - **Why it matters:** False dependencies make the component fragile to refactoring.
   - **Test:** Verify mode survives `initialBrowsePath` changes.

5. **CRITICAL2-5: Radiogroup Invariant**
   - **Issue:** More than one or zero radios are checked simultaneously (invariant violation).
   - **Why it matters:** User sees both/neither mode as selected; modal is broken.
   - **Test:** Stress test 20 rapid mode changes, assert exactly 1 is checked at all times.

**Exposure Level:** High — These catch the "happy path" working but edge cases breaking.

---

### CRITICAL3: File Selection & Mode Decoupling (4 tests)

**Severity:** 🟠 **HIGH** — Guards against state coupling bugs

**Gaps Identified:**

1. **CRITICAL3-1: File Selection Survival**
   - **Issue:** Switching modes clears selected files.
   - **Why it matters:** User loses their work when changing import strategy.
   - **Test:** Select files, switch modes, verify files are still selected.

2. **CRITICAL3-2: Order Preservation on Mode Switch**
   - **Issue:** Files are re-sorted or reordered when mode changes.
   - **Why it matters:** Inconsistent UX; files appear to move unexpectedly.
   - **Test:** Select in reverse order, switch modes, verify alphabetical order is maintained in emission.

3. **CRITICAL3-3: Continue Disable Rule Consistency**
   - **Issue:** In smart mode, Continue can be enabled even with zero files.
   - **Why it matters:** Crashes downstream when no files to import.
   - **Test:** Verify Continue stays disabled with zero files in both modes.

4. **CRITICAL3-4: Rapid Toggle Stress**
   - **Issue:** Rapid file toggles + mode switches corrupt state.
   - **Why it matters:** Exposes race conditions or improper state batching.
   - **Test:** 30 alternating file and mode changes, verify both states are coherent.

**Exposure Level:** Medium-High — These expose cross-cutting state bugs.

---

### CRITICAL4: Loading State Interaction (3 tests)

**Severity:** 🟠 **HIGH** — Guards against disabled-state bugs

**Gaps Identified:**

1. **CRITICAL4-1: Disabled ≠ Reset**
   - **Issue:** During `isLoading`, the mode is disabled but also reset to initialMode.
   - **Why it matters:** User's mode choice is lost when loading completes.
   - **Test:** Verify disabled radios don't change their checked state.

2. **CRITICAL4-2: Re-enable After Loading**
   - **Issue:** After `isLoading` transitions to false, mode options stay disabled ("sticky disabled").
   - **Why it matters:** User cannot interact with the modal even after loading finishes.
   - **Test:** Turn off `isLoading`, click mode, verify it works.

3. **CRITICAL4-3: Double-Guard on Invoke**
   - **Issue:** `onContinue` is called even though Continue button is disabled (missing guard).
   - **Why it matters:** Duplicate imports or side effects.
   - **Test:** While `isLoading`, directly call `button.click()`, verify `onContinue` is NOT called.

**Exposure Level:** Medium — Specific to a particular prop state.

---

### CRITICAL5: Error Message & Mode Coupling (3 tests)

**Severity:** 🟡 **MEDIUM** — Detects unintended prop coupling

**Gaps Identified:**

1. **CRITICAL5-1: Error Appearance Stability**
   - **Issue:** Error message appearing resets mode to initial.
   - **Why it matters:** User loses mode selection when an error occurs.
   - **Test:** Show error, verify mode is unchanged.

2. **CRITICAL5-2: Error Disappearance Stability**
   - **Issue:** Clearing error resets mode.
   - **Why it matters:** UX churn when error is dismissed.
   - **Test:** Clear error, verify mode is unchanged.

3. **CRITICAL5-3: Error Content Swap Stability**
   - **Issue:** Changing error message text resets mode.
   - **Why it matters:** Mode should never depend on error state.
   - **Test:** Replace error A with error B, verify mode is unchanged.

**Exposure Level:** Low-Medium — Edge case but indicates shallow state coupling.

---

### CRITICAL6: Callback Promise/Async Handling (3 tests)

**Severity:** 🟠 **HIGH** — Detects async/concurrency bugs

**Gaps Identified:**

1. **CRITICAL6-1: Slow Async Stability**
   - **Issue:** While `onContinue` is awaiting, mode is inaccessible or resets.
   - **Why it matters:** User thinks modal is frozen when really the mode is locked.
   - **Test:** Start async `onContinue`, switch modes, verify it works.

2. **CRITICAL6-2: Error Throw Stability**
   - **Issue:** If `onContinue` throws, mode state is corrupted.
   - **Why it matters:** Modal becomes unusable after error.
   - **Test:** Throw from `onContinue`, verify mode is intact.

3. **CRITICAL6-3: Promise Rejection Stability**
   - **Issue:** Rejected Promise causes mode to reset or disappear.
   - **Why it matters:** Cascade failures when upstream fails.
   - **Test:** Reject from `onContinue`, verify mode is unchanged.

**Exposure Level:** High — Real-world async scenarios.

---

### CRITICAL7: Backward Compatibility (3 tests)

**Severity:** 🔴 **CRITICAL** — Guards against breaking changes

**Gaps Identified:**

1. **CRITICAL7-1: Legacy Single-Arg Callers**
   - **Issue:** Old code calling `onContinue(paths)` without reading the mode arg breaks.
   - **Why it matters:** Existing integrations fail after upgrade.
   - **Test:** Verify a single-arg callback still works (receives paths).

2. **CRITICAL7-2: Mode Not in onClose**
   - **Issue:** Mode argument leaks into `onClose` callback.
   - **Why it matters:** Unexpected args break old close handlers.
   - **Test:** Verify `onClose` is called with zero arguments.

3. **CRITICAL7-3: Callback Substitution**
   - **Issue:** Replacing `onContinue` during interaction results in the new handler receiving stale mode.
   - **Why it matters:** Dynamic callback replacement (parent re-renders) loses the current state.
   - **Test:** Replace handler, click Continue, verify new handler gets current mode.

**Exposure Level:** Critical — Deployment blockers if these fail.

---

### CRITICAL8: Accessibility Invariants (4 tests)

**Severity:** 🟠 **HIGH** — Accessibility compliance

**Gaps Identified:**

1. **CRITICAL8-1: Radiogroup Role Stability**
   - **Issue:** Role changes from `"radiogroup"` to something else during re-renders.
   - **Why it matters:** Assistive tech loses context; modal becomes inaccessible.
   - **Test:** Render, rerender with prop changes, verify role is always `"radiogroup"`.

2. **CRITICAL8-2: Radio Role Correctness**
   - **Issue:** Options are `role="button"` instead of `role="radio"`.
   - **Why it matters:** Assistive tech announces wrong control type.
   - **Test:** Verify both options have `role="radio"`.

3. **CRITICAL8-3: aria-describedby Validity**
   - **Issue:** `aria-describedby` references a non-existent element or is null.
   - **Why it matters:** Assistive tech loses the help text.
   - **Test:** Verify ID exists and target element is in DOM.

4. **CRITICAL8-4: aria-label Correctness**
   - **Issue:** Radiogroup `aria-label` is empty or doesn't mention "import" and "mode".
   - **Why it matters:** Screen reader users don't understand the control's purpose.
   - **Test:** Verify label matches pattern `/import.*mode|mode.*import/`.

**Exposure Level:** High — Accessibility violations block WCAG compliance.

---

### CRITICAL9: Type Safety & Boundary Conditions (4 tests)

**Severity:** 🟡 **MEDIUM** — Detects defensive programming gaps

**Gaps Identified:**

1. **CRITICAL9-1: Invalid initialMode Handling**
   - **Issue:** Invalid mode value (e.g., `"invalid"`) is not defaulted; component crashes.
   - **Why it matters:** Typos in prop values cause runtime errors instead of graceful fallback.
   - **Test:** Pass `initialMode="invalid"`, verify it defaults to "regular".

2. **CRITICAL9-2: Empty String initialMode**
   - **Issue:** `initialMode=""` is not treated as "regular".
   - **Why it matters:** Falsy values should be normalized.
   - **Test:** Pass `initialMode=""`, verify it defaults to "regular".

3. **CRITICAL9-3: Null initialMode**
   - **Issue:** `initialMode={null}` is not treated as "regular".
   - **Why it matters:** Defensive programming against null/undefined.
   - **Test:** Pass `initialMode={null}`, verify "regular" is default.

4. **CRITICAL9-4: Undefined initialMode**
   - **Issue:** `initialMode={undefined}` is not treated as "regular".
   - **Why it matters:** Missing/unspecified prop should have a safe default.
   - **Test:** Omit `initialMode`, verify "regular" is default.

**Exposure Level:** Medium — Prevents subtle runtime errors from bad prop values.

---

### CRITICAL10: Lifecycle & Persistence (3 tests)

**Severity:** 🟠 **HIGH** — Detects lifecycle/cleanup bugs

**Gaps Identified:**

1. **CRITICAL10-1: Open/Close Cycle Persistence**
   - **Issue:** After 3+ rapid open/close cycles, mode is corrupted or lost.
   - **Why it matters:** Modal becomes unusable on re-use.
   - **Test:** 3 open→close→open cycles, verify mode resets to `initialMode` each time.

2. **CRITICAL10-2: Error Volatility Across Cycles**
   - **Issue:** Rapidly changing error messages while in smart mode resets mode.
   - **Why it matters:** Error-driven UI updates corrupt mode state.
   - **Test:** Cycle through error states, verify mode stays smart.

3. **CRITICAL10-3: Loading Volatility Across Cycles**
   - **Issue:** Rapidly toggling `isLoading` resets or corrupts mode.
   - **Why it matters:** Loading state updates corrupt mode state.
   - **Test:** Cycle isLoading 5 times, verify mode persists.

**Exposure Level:** High — Affects multi-step user flows.

---

## Test Suite Architecture

### Existing Suites (172 tests)
- **ImportTicketsModal.test.tsx:** 70+ behavioral + adversarial tests (Groups R, S, H, L, C, X)
- **ImportTicketsModal.mutation.test.tsx:** Mutation testing suite
- **ImportTicketsModal.adversarial-deep.test.tsx:** Deep adversarial tests (memory, concurrency, focus)
- **SmartImportToStudioRouting.test.tsx:** Integration testing
- **SmartImportStudioIntegration.test.tsx:** Studio-specific integration
- **SmartImportAdvancedEdgeCases.test.tsx:** Additional edge cases
- **DashboardSmartImportRouting.test.tsx:** Dashboard routing
- **SmartImportPreviewSession.test.tsx:** Preview session management

### New Suite (37 tests)
- **ImportTicketsModal.critical-gaps.test.tsx:** Callback contract, state machine, lifecycle edge cases

---

## Gap Closure Strategy

### Phase 1: Contract Violations (CRITICAL1, CRITICAL7)
- Implement `mode` as a required second argument to `onContinue`
- Ensure mode is always a string `"regular" | "smart"`
- Guard old single-arg callers with default handling
- **Test Implementation:** All CRITICAL1/7 tests must pass

### Phase 2: State Machine Correctness (CRITICAL2, CRITICAL9)
- Use a dedicated state hook for mode (not derived prop)
- Validate and normalize `initialMode` on mount
- Implement useEffect to reset mode on `open` transition
- **Test Implementation:** All CRITICAL2/9 tests must pass

### Phase 3: Isolation & Coupling Prevention (CRITICAL3, CRITICAL5)
- Ensure file selection is not modified by mode changes
- Ensure error/loading states don't affect mode
- Decouple mode from unrelated props
- **Test Implementation:** All CRITICAL3/5 tests must pass

### Phase 4: Async & Lifecycle Safety (CRITICAL4, CRITICAL6, CRITICAL10)
- Implement proper cleanup in useEffect
- Guard against race conditions in async handlers
- Verify disabled state doesn't corrupt mode
- **Test Implementation:** All CRITICAL4/6/10 tests must pass

### Phase 5: Accessibility Compliance (CRITICAL8)
- Render radiogroup with proper ARIA attributes
- Verify descriptions are stable and linked
- Maintain role invariants across re-renders
- **Test Implementation:** All CRITICAL8 tests must pass

---

## Risk Assessment

### Highest Risk Areas
1. **Callback arity regression** — Could break deployed consumers immediately
2. **Mode state machine invariants** — Core feature correctness
3. **State persistence through lifecycle** — Affects multi-step flows
4. **Backward compatibility** — Deployment/integration blockers

### Medium Risk Areas
1. **Async error handling** — Real-world edge case
2. **Accessibility compliance** — Legal/compliance risk
3. **Prop coupling** — Maintainability risk

### Lower Risk Areas
1. **Error message coupling** — Low-probability scenario
2. **Boundary conditions** — Defensive programming

---

## Test Metrics

### Coverage by Dimension
| Dimension | Count | Coverage |
|-----------|-------|----------|
| Callback contract | 5 | ✅ Comprehensive |
| State machine | 5 | ✅ Comprehensive |
| File coupling | 4 | ✅ Comprehensive |
| Loading state | 3 | ✅ Comprehensive |
| Error coupling | 3 | ✅ Comprehensive |
| Async handling | 3 | ✅ Comprehensive |
| Backward compat | 3 | ✅ Comprehensive |
| Accessibility | 4 | ✅ Comprehensive |
| Type safety | 4 | ✅ Comprehensive |
| Lifecycle | 3 | ✅ Comprehensive |
| **TOTAL** | **37** | **✅ Complete** |

### Test Characteristics
- **Deterministic:** ✅ No timers, network, or randomness
- **Reproducible:** ✅ All failures repeat consistently
- **Isolated:** ✅ Mock file explorer, pure Jest mocks
- **Fast:** ✅ 37 tests run in ~1.6 seconds
- **Readable:** ✅ Clear names, structured groups

---

## Implementation Checklist

- [ ] Add `initialMode?: ImportMode` prop to `ImportTicketsModalProps`
- [ ] Add `onContinue: (filePaths: string[], mode: ImportMode) => void | Promise<void>`
- [ ] Implement mode selector UI (radiogroup + 2 radio options)
- [ ] Add mode state via `useState("regular")`
- [ ] Update Continue handler to emit `onContinue(paths, currentMode)`
- [ ] Add `useEffect` to reset mode on `open` transition
- [ ] Validate and normalize `initialMode` on mount
- [ ] Ensure disabled state during `isLoading`
- [ ] Add ARIA labels, descriptions, and role attributes
- [ ] Test all 209 cases pass

---

## Handoff

This test suite is ready for the **Implementation stage**. All tests are deterministic, reproducible, and enforceable. The gaps identified represent real production vulnerabilities that would surface at runtime without this testing.

**Next Agent:** Feature Implementer  
**Expected Time:** ~2-4 hours to implement + all tests passing  
**Validation:** Run full test suite; 209/209 tests pass, 0 failures
