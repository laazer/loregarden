# Test Break Analysis: Ticket #39 - Preview State for Imported Tickets

**Date:** 2026-07-10  
**Stage:** test_break (adversarial testing phase)  
**Agent:** test_breaker

---

## Executive Summary

The existing test suites (`ImportedTicketsPreviewState.adversarial.test.tsx` and `.mutation.test.tsx`) provide solid baseline coverage for the three acceptance criteria. However, analysis reveals **critical gaps** where false confidence from mocking and incomplete integration testing could allow subtle bugs to reach production.

**Key Findings:**
- ✅ AC1 (Preview UI recognition): Well covered
- ✅ AC2 (Read-only content): Moderately covered, gaps in integration
- ✅ AC3 (Finalize locking): Well covered at component level, weak on end-to-end
- ⚠️ **Integration gaps**: Mock isolation hides prop-passing bugs
- ⚠️ **Timing issues**: No race conditions in real async scenarios
- ⚠️ **Security gaps**: XSS assumptions, copy/paste scenarios missing
- ⚠️ **A11y assumptions**: Tests assume aria-labels/titles exist without verifying rendering

---

## Category 1: Mock-Isolated False Confidence

### Problem
Tests mock `apiClient.finalizeHierarchy()`, so they never validate:
- Whether disabled button actually prevents DOM interaction
- Whether aria-disabled is enforced (different from `disabled` attribute)
- Whether disabled state propagates through prop chains
- Whether confirmation dialog is rendered before API calls

### Evidence
**File:** ImportedTicketsPreviewState.adversarial.test.tsx:414-424
```javascript
it("ADVA-PREVIEW-3.5: finalize button click is prevented when disabled", async () => {
  const finalizeBtn = getFinalizeButton();
  if (finalizeBtn) {
    await user.click(finalizeBtn);
    expect(apiClient.finalizeHierarchy).not.toHaveBeenCalled();
  }
});
```

**Problem:** If `disabled` attribute is missing but button has `aria-disabled="true"`, this test passes falsely. Real user can still click.

### Weakness Pattern
- Tests check mocked API wasn't called, not whether user interaction was actually prevented
- No tests verify `<button disabled>` HTML attribute exists
- No tests verify button doesn't respond to Enter/Space keys when disabled
- No tests for `onClick` handler removal vs. conditional returns

### Missing Hardening Tests
Need integration tests that:
1. Verify button doesn't call onClick when disabled
2. Test keyboard navigation (Tab, Enter, Space) on disabled button
3. Verify screen reader announces button as disabled
4. Test that clicking a disabled button doesn't trigger unintended side effects

---

## Category 2: DOM Selector Brittleness

### Problem
Tests use loose regex patterns to find elements, which pass if ANY matching text exists.

### Evidence
**File:** ImportedTicketsPreviewState.adversarial.test.tsx:96-103
```javascript
function getFinalizeButton(): HTMLElement | null {
  return screen.queryByRole("button", { name: /finalize|create.*commit/i });
}
```

**Problem:** This finds any button with "create", "commit", "finalize" in name. If component renders multiple buttons (Undo, Create Draft, Create Milestone, Commit Changes), test might grab wrong button.

### Test Failure Scenarios
1. Button has text "Create Draft" - matches `/create.*commit/i`? No, but close
2. Button is "Commit to Workspace" - matches both patterns
3. Typo: "Finalize" → "Finalise" (UK spelling) - pattern fails silently
4. Internationalization: translated button text breaks selector

### Missing Precision Tests
Need tests that:
1. Verify exact button label matches (not substring)
2. Test with button located by data-testid (more stable)
3. Verify button position in DOM (first actionable button in footer, etc.)
4. Test selector robustness against UI variations

---

## Category 3: Timing & Race Condition Gaps

### Problem
Tests use `rerender()` for state changes but don't test realistic async scenarios.

### Evidence
**File:** ImportedTicketsPreviewState.adversarial.test.tsx:456-476
```javascript
it("ADVA-PREVIEW-3.8: finalize button state updates when preview flag changes", async () => {
  const { rerender } = renderStudioWithPreview({ isPreview: true });
  let finalizeBtn = getFinalizeButton();
  expect(finalizeBtn).toBeDisabled();

  rerender(<TicketStudioPanel ... isPreview={false} />);
  finalizeBtn = getFinalizeButton();
  if (finalizeBtn) {
    expect(finalizeBtn).not.toBeDisabled();
  }
});
```

**Problem:** `rerender()` is synchronous. Doesn't test:
- What if parent component updates isPreview while finalize API call is in-flight?
- What if preview state changes during confirmation dialog interaction?
- What if props update faster than React reconciliation?

### Real-World Failure Scenarios
1. User clicks "Finalize" (button enabled)
2. Confirmation dialog opens
3. Before user can click "Confirm", isPreview prop changes to true
4. Component re-renders, finalize button disabled
5. User still has confirmation dialog open
6. What happens if they click "Confirm" now?
   - Should be blocked
   - Test doesn't verify this

### Missing Race Condition Tests
Need tests with real async/timing:
1. API call pending → prop changes → test interaction
2. Confirmation dialog open → parent state changes → test behavior
3. Multiple async operations (confirmation + preview state change)
4. Unmount during loading scenarios

---

## Category 4: Security & XSS Assumptions

### Problem
Tests assume XSS protection works but don't validate implementation.

### Evidence
**File:** ImportedTicketsPreviewState.adversarial.test.tsx:341-355
```javascript
it("ADVA-PREVIEW-2.10: handles special characters in imported ticket data", () => {
  renderStudioWithPreview({
    isPreview: true,
    importedTickets: [
      { external_id: "t-xss", title: "<img src=x onerror='alert(1)'>" },
    ],
  });

  expect(screen.queryByText("alert")).not.toBeInTheDocument();
});
```

**Problem:** Test checks if "alert" text doesn't appear, but:
- XSS payload might not render as text (e.g., rendered as HTML)
- Test doesn't verify content is HTML-escaped
- Test doesn't check if script actually ran
- Missing: test that verifies content is text node, not innerHTML

### XSS False Positive Scenarios
1. Component uses `dangerouslySetInnerHTML` for preview content
   - XSS script runs
   - But "alert" text never renders as DOM text
   - Test passes falsely
2. Component escapes title but not other fields
   - Only partial protection
3. Component sanitizes on render but not on storage
   - Attack vector through data persistence

### Missing Security Tests
Need tests that:
1. Verify all user-controlled fields are text content (not HTML)
2. Test different XSS vectors (SVG, iframe, event handlers, CSS)
3. Verify sanitization is applied consistently across all fields
4. Test that preview content can't escape its container

---

## Category 5: Accessibility Assumptions

### Problem
Tests assume components render accessible attributes but don't verify they're actually present in the implementation.

### Evidence
**File:** ImportedTicketsPreviewState.adversarial.test.tsx:380-394
```javascript
it("ADVA-PREVIEW-3.3: disabled finalize button has accessibility info", () => {
  renderStudioWithPreview({ isPreview: true });
  const finalizeBtn = getFinalizeButton();
  if (finalizeBtn) {
    expect(finalizeBtn).toHaveAttribute("disabled");
    const ariaLabel = finalizeBtn.getAttribute("aria-label") || "";
    const title = finalizeBtn.getAttribute("title") || "";
    expect(ariaLabel || title || finalizeBtn.textContent).toMatch(
      /confirm|preview|disable|must/i,
    );
  }
});
```

**Problem:**
- `if (finalizeBtn)` gates the actual assertion - test passes if button doesn't exist
- Component might not implement aria-label at all
- Test assumes some accessibility support, not requires it
- No verification of descriptive text quality

### A11y False Confidence
1. Test checks `ariaLabel || title || textContent` - one of these might be too permissive
2. If button text is just "Finalize" and aria-label is missing, test might still pass
3. No test for screen reader announcement when button state changes
4. No test for high-contrast mode visibility

### Missing A11y Tests
Need tests that:
1. Verify aria-disabled vs HTML disabled distinction
2. Test screen reader content (role announcement)
3. Verify high-contrast mode styling
4. Test keyboard navigation order (Tab sequence)
5. Verify descriptive text is meaningful, not generic

---

## Category 6: Property Passing & Type Safety

### Problem
Tests use `@ts-ignore` to add preview props that don't exist in TicketStudioPanelProps yet. Tests don't verify props are correctly wired through component hierarchy.

### Evidence
**File:** ImportedTicketsPreviewState.adversarial.test.tsx:75-94
```javascript
function renderStudioWithPreview(
  overrides: PreviewSessionProps = {},
) {
  const props: TicketStudioPanelProps = {
    workspaceSlug: "loregarden",
    onClose: jest.fn(),
    // @ts-ignore - preview state not yet typed, we're adding it
    isPreview: overrides.isPreview ?? false,
    importedTickets: overrides.importedTickets ?? [],
    ...overrides,
  };
  
  return render(
    <MemoryRouter>
      <TicketStudioPanel {...props} />
    </MemoryRouter>,
  );
}
```

**Problems:**
1. Tests assume props exist on TicketStudioPanel but use `@ts-ignore`
2. No tests verify props are passed to child components
3. No tests for prop forwarding through HOCs or context
4. No tests for prop validation or type enforcement at boundaries

### Type Safety False Confidence
1. Component might receive isPreview prop but never use it
2. Tests pass because they only check final DOM, not implementation
3. No verification of TypeScript type checking at component level
4. No tests for prop deprecation or migration paths

### Missing Type & Props Tests
Need tests that:
1. Verify props are required in TicketStudioPanelProps type
2. Test prop forwarding through component tree
3. Verify child components receive correct props
4. Test TypeScript compilation with preview props
5. Verify no prop drilling issues in deeply nested components

---

## Category 7: Missing Mock-Resistant Integration Tests

### Problem
Tests extensively mock API but don't test real dependencies.

### Missing Scenarios
1. **API Integration**: 
   - What if finalizeHierarchy API changes signature?
   - Tests don't catch breaking changes (mock always works)
   
2. **Context/Provider Integration**:
   - Tests don't verify preview state works with real query client
   - No tests for stale state in React Query cache
   
3. **Navigation Integration**:
   - Tests mock useNavigate but don't verify actual navigation works
   - No tests for history state preservation
   
4. **Data Flow**:
   - No tests for how preview state flows from route params
   - No tests for preview state persisting in session storage

### Missing Integration Tests
Need tests that:
1. Verify API call payload when isPreview changes
2. Test with real query client (not mocked)
3. Verify preview state flows correctly from parent route
4. Test session persistence (page reload)
5. Test concurrent preview sessions in same workspace

---

## Category 8: Incomplete Edge Cases

### Missing Negative Test Scenarios

#### 1. Button State Contradictions
```javascript
// Test missing: what if isPreview=false but isReadOnly=true?
// Should finalize button be enabled?
// Or should readonly prevent finalization?
// Current tests don't clarify the interaction
```

#### 2. Empty/Null Edge Cases with Operations
```javascript
// Test missing: user navigates to imported ticket preview,
// then clears all tickets from importedTickets array
// while preview state is still true.
// Does button remain disabled? Can user still see the lock?
```

#### 3. Permission-Based Scenarios
```javascript
// Test missing: user has permission to finalize in non-preview mode
// but lacks permission in preview mode (extra confirmation needed).
// Current tests assume permission is binary.
```

#### 4. Multi-Workspace Edge Cases
```javascript
// Test missing: user switches workspaces while preview is active.
// Does preview state persist? Should it?
// Current tests render single workspace only.
```

#### 5. Preview with Corrupted Session
```javascript
// Test missing: preview state is true, but session data is null/undefined.
// Should component render error boundary or fallback UI?
```

---

## Category 9: State Persistence & Replay

### Problem
Tests don't verify state persists correctly across page reloads or navigations.

### Missing Scenarios
1. User in preview session
2. User clicks back/forward in browser
3. User closes and reopens browser tab
4. Should preview state be restored?

**Current tests:** All render fresh components, never test persistence.

### Missing Persistence Tests
Need tests that:
1. Verify preview state persists in URL params
2. Test session storage/local storage preservation
3. Verify state survives page reload
4. Test history API compatibility

---

## Critical Test Gaps - Summary Table

| Gap | Severity | Category | Impact |
|-----|----------|----------|---------|
| Button disabling not verified | HIGH | Mock Isolation | False finalization prevention |
| Keyboard interaction missing | HIGH | A11y | Disabled button bypassed via keyboard |
| Race conditions (async) | HIGH | Timing | State corruption during finalization |
| XSS escaping not validated | HIGH | Security | Script injection in preview content |
| Props not wired through hierarchy | MEDIUM | Types | Props ignored by implementation |
| Multiple button selectors break | MEDIUM | DOM Brittleness | Tests grab wrong button |
| API changes not caught | MEDIUM | Integration | Breaking API changes undetected |
| Session persistence untested | MEDIUM | State | Preview state lost on reload |
| Permission scenarios missing | LOW | Edge Cases | Incomplete coverage |
| Workspace switching untested | LOW | Edge Cases | Multi-workspace bugs |

---

## Recommended Additional Tests

### Priority 1: Security & Interaction Hardening
1. **Button Interaction Tests** - Verify button is actually disabled in DOM
2. **Keyboard Navigation** - Tab, Enter, Space key handling
3. **XSS Validation** - Content is text nodes, not HTML
4. **API Payload Tests** - Verify correct data sent to finalizeHierarchy

### Priority 2: Integration & Real Async
1. **Real Query Client Tests** - Remove API mock, use test server
2. **Navigation Integration** - Verify actual route changes
3. **Race Condition Tests** - Async state updates with real timers
4. **Session Persistence** - Local storage, URL params

### Priority 3: Edge Cases & Scenarios
1. **Keyboard-Only Navigation** - No mouse clicks
2. **Screen Reader Testing** - aria announcements
3. **High-Contrast Mode** - Visual verification
4. **Multi-Window Scenarios** - Concurrent preview sessions

---

## Next Steps for Implementation

1. **Before writing implementation:** Review this analysis with the implementation team
2. **Run existing tests first:** Ensure baseline tests catch obvious bugs
3. **Add hardening tests** from Priority 1 before implementation review
4. **Use real dependencies** (query client, API) in integration tests
5. **Measure mutation coverage:** Run mutation testing tool to verify test effectiveness

---

## Files to Review/Extend

- ✅ `ImportedTicketsPreviewState.adversarial.test.tsx` (baseline exists)
- ✅ `ImportedTicketsPreviewState.mutation.test.tsx` (baseline exists)
- ⬜ `ImportedTicketsPreviewState.integration.test.tsx` (NEEDS CREATION)
- ⬜ `ImportedTicketsPreviewState.keyboard.test.tsx` (NEEDS CREATION)
- ⬜ `ImportedTicketsPreviewState.security.test.tsx` (NEEDS CREATION)

---

## Severity & Recommendation

**OVERALL RISK:** Medium-High

**Recommendation:** The existing test suites provide good starting points but are **not sufficient** for production confidence. The gaps identified above (especially mock isolation, keyboard interaction, and race conditions) represent realistic attack vectors that could let subtle bugs through.

**Before implementation approval:**
1. Add integration tests with real dependencies
2. Add keyboard navigation tests
3. Add security/XSS validation tests
4. Verify all assertions would fail on incorrect implementation

