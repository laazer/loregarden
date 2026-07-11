# Ticket 39 Execution Plan: Implement Preview State for Imported Tickets in Studio

**Ticket:** 39-implement-preview-state-for-imported-tickets-in-  
**Stage:** Specification (SPEC)  
**Agent:** Planner  
**Date:** 2026-07-10  
**Status:** Plan Ready for Review

---

## Project Overview

**Feature:** Track and display imported tickets in preview (not-yet-finalized) state within Studio, with explicit confirmation requirements before finalization.

**Scope:** 
- Frontend component updates to support preview state
- UI enhancements for read-only content display
- Finalize button state management and confirmation flow
- Type system updates for new props and data structures

**Constraints:**
- Must not modify database schema (already done in migration 0013)
- Must not change backend API contracts (already implemented)
- Must pass all 600+ existing tests
- All changes must be in frontend layer only (at this stage)

---

## Tasks

| # | Task Objective | Assigned Agent | Input | Expected Output | Dependencies | Success Criteria | Risks / Assumptions | Clarifying Questions |
|---|---|---|---|---|---|---|---|---|
| **1** | **Define and export TypeScript types for preview feature** | Frontend Implementer | <ul><li>TICKET_39_SPECIFICATION.md</li><li>API response schema</li><li>Test expectations</li></ul> | <ul><li>`ImportedTicket` interface exported from `client/src/api/types.ts`</li><li>`TicketStudioPanelProps` interface exported</li><li>`TicketStudioDraftModalProps` updated with `isPreview` prop</li><li>Types document in code comments</li></ul> | None | <ul><li>All types compile without errors</li><li>Types match API response structure</li><li>Props interfaces include all fields used in tests</li><li>`npm run tsc --noEmit` passes</li></ul> | <ul><li>Assumption: Using SQLModel-style naming conventions</li><li>Assumption: Re-using existing type patterns</li></ul> | <ul><li>Should `imported_tickets_json` be parsed at API layer or component layer?</li><li>Should there be a separate `ImportedTicketView` type for API responses vs internal usage?</li></ul> |
| **2** | **Wire preview props through TicketStudioPanel component** | Frontend Implementer | <ul><li>Type definitions from Task 1</li><li>Existing component code</li><li>Test expectations from integration tests</li></ul> | <ul><li>TicketStudioPanel accepts `isPreview`, `importedTickets`, `onPreviewChange` props</li><li>Props extracted from API response (TicketStudioSessionView)</li><li>State initialized from props</li><li>Props properly flow to child components</li><li>Console errors cleared</li></ul> | 1 | <ul><li>Component renders without errors when props provided</li><li>Props flow correctly to children</li><li>State stays in sync with props</li><li>No TypeScript errors in component</li><li>Integration tests reach UI render step (not stuck at setup)</li></ul> | <ul><li>Assumption: Component uses React hooks</li><li>Assumption: QueryClient provides session data</li></ul> | <ul><li>How should `onPreviewChange` be triggered? (button click? API response?)</li><li>Should preview state be managed locally or pulled from session?</li></ul> |
| **3** | **Implement preview badge UI component** | Frontend Implementer | <ul><li>Acceptance criteria AC1</li><li>Existing badge/state-label patterns in codebase</li><li>Adversarial tests for badge behavior</li></ul> | <ul><li>New component: `PreviewStateBadge.tsx` (or integrated into session header)</li><li>Renders when `isPreview=true`</li><li>Shows warning/info styling</li><li>Includes clear text explanation</li><li>Accessible (aria labels, semantic HTML)</li></ul> | 2 | <ul><li>Badge renders only when `isPreview=true`</li><li>Badge text is visible and readable</li><li>Badge styling matches design system</li><li>a11y tests pass (WCAG AA)</li><li>Adversarial tests ADVA-PREVIEW-1.1+ pass</li></ul> | <ul><li>Assumption: Using existing CSS/styling patterns</li><li>Assumption: Badge should persist across navigation</li></ul> | <ul><li>What styling (color, icon) should indicate preview state?</li><li>Should badge be dismissible or always-on?</li></ul> |
| **4** | **Implement finalize button disabled state logic** | Frontend Implementer | <ul><li>Finalize button element location</li><li>Current button implementation code</li><li>Integration tests INT-PREVIEW-1</li></ul> | <ul><li>Finalize button uses HTML `disabled` attribute</li><li>Button disabled when `isPreview=true`</li><li>Button enabled when `isPreview=false`</li><li>Click handler does not fire when disabled</li><li>State persists across re-renders</li></ul> | 2 | <ul><li>Button has `disabled` attribute in DOM when `isPreview=true`</li><li>Button does not have `disabled` when `isPreview=false`</li><li>INT-PREVIEW-1.1 (button DOM attribute) passes</li><li>INT-PREVIEW-1.2 (disabled click) passes</li><li>INT-PREVIEW-1.3 (enabled click) passes</li><li>INT-PREVIEW-1.4 (persistence) passes</li></ul> | <ul><li>Assumption: Button element exists and is findable</li><li>Assumption: onClick handler can be conditional</li></ul> | <ul><li>Should button be hidden or disabled (spec says disabled)?</li><li>Should disabled button have tooltip explaining why?</li></ul> |
| **5** | **Create imported tickets display panel/sidebar** | Frontend Implementer | <ul><li>AC2 requirement</li><li>ImportedTicket type definition</li><li>ADVA-PREVIEW-2 tests</li><li>Existing sidebar/panel patterns</li></ul> | <ul><li>New component or panel: renders imported tickets list</li><li>Displays external_id, title, type badge, priority, criteria</li><li>No edit controls visible</li><li>Handles empty array gracefully</li><li>Handles 500+ items (efficient rendering)</li><li>XSS-safe rendering of special characters</li></ul> | 2 | <ul><li>Imported tickets render when `importedTickets.length > 0`</li><li>Each ticket shows required fields (id, title, type, priority)</li><li>No form inputs or edit buttons visible</li><li>ADVA-PREVIEW-2.1+ tests pass</li><li>Performance acceptable with 500+ items</li><li>Special characters (`, <, >, &) properly escaped</li></ul> | <ul><li>Assumption: Using React safely (auto-escapes by default)</li><li>Assumption: Can reuse existing card/list components</li></ul> | <ul><li>Should imported tickets be in a sidebar, tab, or modal?</li><li>Should there be search/filter on imported tickets?</li><li>Should full description be visible or expandable?</li></ul> |
| **6** | **Apply read-only styling and disable editing in preview mode** | Frontend Implementer | <ul><li>TicketStudioDraftModal component</li><li>All form input patterns in component</li><li>Read-only mode implementation</li></ul> | <ul><li>All inputs in draft modal disabled when `isPreview=true`</li><li>Visual indication of read-only state (greyed out, etc.)</li><li>Modal title indicates "Preview/Read-only" mode</li><li>Save button hidden or disabled in preview mode</li><li>Consistent styling across all inputs</li></ul> | 2, 5 | <ul><li>Form inputs (text, select, textarea) have `disabled` attribute when preview</li><li>Buttons that modify state are disabled or hidden</li><li>UI clearly communicates read-only status</li><li>No input changes possible when `isPreview=true`</li><li>ADVA-PREVIEW-2 tests pass (read-only enforcement)</li></ul> | <ul><li>Assumption: Modal prop already has `readOnly` support</li><li>Assumption: Inputs use standard HTML attributes</li></ul> | <ul><li>Should modal be completely hidden or shown in read-only mode?</li><li>Should there be a "Switch to Edit" button (after confirming)?</li></ul> |
| **7** | **Implement confirmation dialog for finalization** | Frontend Implementer | <ul><li>Finalization requirements from AC3</li><li>Existing modal/dialog patterns</li><li>Confirmation test expectations</li></ul> | <ul><li>New component: `FinalizationConfirmDialog.tsx`</li><li>Appears when finalize button clicked and `isPreview=false`</li><li>Title: "Finalize work items?"</li><li>Message: Shows count of items being created</li><li>Buttons: "Cancel" | "Confirm"</li><li>Requires explicit user action (no auto-confirm)</li><li>Integrates with existing modal styling</li></ul> | 4 | <ul><li>Dialog renders when button clicked</li><li>Dialog shows correct item count</li><li>Cancel button closes dialog without action</li><li>Confirm button triggers finalization</li><li>Dialog has proper focus management</li><li>ADVA-PREVIEW-4 tests pass</li></ul> | <ul><li>Assumption: Can reuse existing modal infrastructure</li><li>Assumption: Draft items array is available for count</li></ul> | <ul><li>Should dialog warn specifically about preview origin?</li><li>Should there be a checkbox "Don't show again"?</li><li>Should there be destructive styling on confirm button?</li></ul> |
| **8** | **Integrate finalization API call and state update** | Frontend Implementer | <ul><li>Finalization endpoint from API</li><li>Existing mutation patterns (useMutation)</li><li>Navigation utilities</li></ul> | <ul><li>Finalize button click calls finalization endpoint</li><li>Passes correct workspace context and draft items</li><li>Updates session `is_preview` to false on success</li><li>Navigates to finalized content on success</li><li>Error handling with user-facing message</li><li>Loading state with disabled button</li></ul> | 4, 7 | <ul><li>Finalization endpoint called with correct payload</li><li>INT-PREVIEW-2 tests pass (API verification)</li><li>Session updates in QueryClient after success</li><li>Navigation occurs to finalized content</li><li>Error state displays appropriately</li><li>Loading spinner shows during request</li></ul> | <ul><li>Assumption: API endpoint is stable and documented</li><li>Assumption: Can use QueryClient mutations pattern</li></ul> | <ul><li>What should happen if finalization partially fails?</li><li>How long should we wait before timeout?</li><li>Should failed items be retryable?</li></ul> |
| **9** | **Handle state transitions and race conditions** | Frontend Implementer | <ul><li>INT-PREVIEW-3 async state tests</li><li>State management in TicketStudioPanel</li><li>Mutation/query handling patterns</li></ul> | <ul><li>State transitions are atomic (no partial updates)</li><li>Rapid preview state toggles handled correctly</li><li>State changes during API calls don't cause race conditions</li><li>Imported tickets changes don't lose button state</li><li>Component unmounting during flight handled gracefully</li></ul> | 2, 4, 8 | <ul><li>INT-PREVIEW-3.1 (state change during flight) passes</li><li>INT-PREVIEW-3.2 (rapid transitions) passes</li><li>INT-PREVIEW-3.3 (imported tickets change) passes</li><li>No console errors about state updates</li><li>No memory leaks (cleanup in effects)</li></ul> | <ul><li>Assumption: Using useEffect cleanup patterns</li><li>Assumption: Can abort in-flight requests</li></ul> | <ul><li>Should we debounce state changes?</li><li>Should we queue pending operations?</li></ul> |
| **10** | **Add accessibility attributes and keyboard navigation** | Frontend Implementer | <ul><li>Keyboard tests (45+ tests)</li><li>WCAG AA accessibility standards</li><li>Existing a11y patterns</li></ul> | <ul><li>Disabled button has `aria-disabled="true"`</li><li>Preview badge has `role="status"` or similar</li><li>Dialog has proper ARIA labels and focus trap</li><li>All interactive elements are keyboard accessible</li><li>Tab order is logical</li><li>Escape key closes modals</li></ul> | 3, 4, 7 | <ul><li>All keyboard tests pass</li><li>All interactive elements reachable via Tab</li><li>Disabled state communicated via aria attributes</li><li>Focus visible and clear</li><li>Screen reader announces preview state</li><li>a11y linter reports no violations</li></ul> | <ul><li>Assumption: Using semantic HTML</li><li>Assumption: Can use aria- attributes</li></ul> | <ul><li>Should disabled button be in tab order?</li><li>What should screen readers announce for preview badge?</li></ul> |
| **11** | **Run comprehensive test suite and fix failures** | Frontend Implementer | <ul><li>All 600+ test files</li><li>Implementation from Tasks 1-10</li><li>TICKET_39_SPECIFICATION.md</li></ul> | <ul><li>All 600+ tests passing</li><li>No console errors or warnings</li><li>Coverage report showing all critical paths tested</li><li>Test output document</li></ul> | 1-10 | <ul><li>Integration tests: 50+ passing</li><li>Adversarial tests: 272+ passing</li><li>Mutation tests: 90+ passing</li><li>Keyboard tests: 45+ passing</li><li>Security tests: 60+ passing</li><li>Zero test failures</li><li>No console errors</li></ul> | <ul><li>Assumption: Tests are well-designed and cover implementation</li><li>Assumption: Test failures indicate implementation gaps</li></ul> | <ul><li>Which test failures should be fixed first?</li><li>Are there flaky tests that need special handling?</li></ul> |
| **12** | **Verify acceptance criteria are met** | Frontend Implementer | <ul><li>All working code from Tasks 1-11</li><li>TICKET_39_SPECIFICATION.md acceptance criteria section</li><li>Manual testing checklist</li></ul> | <ul><li>AC1 verification: Preview badge renders, visible, styled, persists</li><li>AC2 verification: Imported tickets display, read-only, complete, escaped, performant</li><li>AC3 verification: Button disabled in preview, confirmation dialog, explicit confirm required</li><li>Manual testing summary</li></ul> | 1-11 | <ul><li>AC1 demo: Badge visible when isPreview=true</li><li>AC2 demo: Imported tickets panel shows with no edit controls</li><li>AC3 demo: Button disabled in preview, confirmation required before finalize</li><li>Manual testing checklist all passing</li><li>No edge cases missed</li></ul> | <ul><li>Assumption: Tests validate ACs</li><li>Assumption: Manual testing validates UX</li></ul> | <ul><li>Should AC verification be automated or manual?</li><li>What specific scenarios must be manually tested?</li></ul> |

---

## Task Dependencies Graph

```
Task 1 (Types)
    ↓
Tasks 2, 3, 4, 5, 6
    ↓
Tasks 7, 8
    ↓
Tasks 9, 10
    ↓
Task 11 (Tests)
    ↓
Task 12 (Verification)
```

**Critical Path:** 1 → 2 → 4 → 7 → 8 → 9 → 11 → 12 (minimum viable path)

**Parallel Opportunities:** 
- Tasks 3, 5, 6 can run in parallel with Task 2 (all depend on Task 1)
- Task 10 can run in parallel with Tasks 7, 8, 9

---

## Phase Breakdown

### Phase 1: Foundation (Tasks 1-2)
**Goal:** Get component accepting and using preview props  
**Effort:** 1-2 hours  
**Blockers:** None  
**Outcome:** Component renders with new props, setup costs eliminated  

### Phase 2: Core Features (Tasks 3-6)
**Goal:** Implement preview UI and read-only mode  
**Effort:** 3-4 hours  
**Blockers:** Phase 1 complete  
**Outcome:** Preview badge visible, imported tickets displayed, button disabled  

### Phase 3: Finalization (Tasks 7-9)
**Goal:** Complete finalization flow with confirmation  
**Effort:** 2-3 hours  
**Blockers:** Phase 2 complete  
**Outcome:** User can confirm and finalize with proper state handling  

### Phase 4: Polish (Tasks 10-12)
**Goal:** Accessibility, tests, verification  
**Effort:** 3-4 hours  
**Blockers:** Phase 3 complete  
**Outcome:** All 600+ tests passing, AC verified  

**Total Estimated Effort:** 9-13 hours (1-2 days)

---

## Risk Assessment

| Risk | Severity | Probability | Mitigation |
|------|----------|-------------|-----------|
| Type system incomplete | Medium | Medium | Task 1 reviews all usages in tests before proceeding |
| Test infrastructure issues | Medium | Low | Use integration.test.tsx as reference pattern |
| Race conditions in state | High | Medium | Extensive testing in Task 9, use useEffect cleanup |
| Accessibility violations | Low | Medium | a11y linter in Task 10, WCAG AA compliance |
| Performance with large datasets | Medium | Low | Virtualization investigation in Task 5 if needed |
| API integration failures | Medium | Low | Mock API in tests, verify endpoints exist |

---

## Success Metrics

### Quantitative
- [ ] 600+ tests passing (100%)
- [ ] 0 console errors or warnings
- [ ] 0 TypeScript compilation errors
- [ ] All 3 acceptance criteria passing
- [ ] 0 accessibility violations

### Qualitative
- [ ] Code review approved
- [ ] Implementation matches specification
- [ ] No edge cases missed
- [ ] User experience is intuitive
- [ ] Code is maintainable

---

## Assumptions & Constraints

### Assumptions
1. Backend API endpoints are stable and functioning
2. Database migration (0013) has been applied
3. Test infrastructure is properly configured
4. Existing component patterns can be reused
5. QueryClient setup in tests follows integration.test.tsx pattern
6. Team has access to test execution environment

### Constraints
1. **No database schema changes** (already done)
2. **No backend API changes** (already implemented)
3. **Frontend only** (this phase)
4. **Must pass all 600+ tests** (hard requirement)
5. **Backward compatible** (no breaking changes)

---

## Clarifying Questions for Stakeholders

1. **Preview Badge Styling**
   - Should badge be warning yellow, info blue, or custom color?
   - Should badge have an icon? Which icon?

2. **Imported Tickets Display**
   - Sidebar, tab, or modal? (Current assumption: sidebar or tab)
   - Should full descriptions be visible or expandable?
   - Do we need search/filter on imported tickets?

3. **Confirmation Dialog**
   - Should dialog warn specifically about "imported" origin?
   - Should there be a checkbox "Don't show again"?
   - What's the exact wording for different item counts?

4. **Error Handling**
   - What should happen if finalization partially fails?
   - Should user be able to retry failed items?
   - How should network errors be displayed?

5. **Performance**
   - Is virtualization needed for 500+ tickets?
   - What's acceptable load time for large sessions?
   - Should we implement pagination?

6. **Navigation**
   - After finalization, where should user navigate?
   - Should we show a success toast/notification?
   - Should we keep history of imported origins?

---

## Glossary

- **Preview State** (`isPreview`): Boolean flag indicating the session contains imported content not yet finalized
- **Imported Tickets**: Original tickets from source that were imported into Studio for use as reference
- **Finalization**: The act of confirming and creating work items from the draft hierarchy
- **Draft Hierarchy**: The modified/proposed structure being designed in Studio before finalization
- **Read-Only Mode**: UI state where no edits are possible (form inputs disabled)

---

## References

- **Specification:** TICKET_39_SPECIFICATION.md
- **Test Results:** TICKET_39_TEST_RESULTS.md
- **Test Break Summary:** TICKET_39_TEST_BREAK_SUMMARY.md
- **Test Files:** `/client/src/components/__tests__/ImportedTicketsPreviewState.*.test.tsx`
- **Database Migration:** Migration 0013 (ticket_studio_preview_state)

---

## Sign-Off

**Plan Created By:** Planner Agent  
**Date:** 2026-07-10  
**Status:** ✅ Ready for Implementation

**Next Steps:**
1. Backend Implementer reviews plan and specification
2. Frontend Implementer begins Phase 1 (type definitions)
3. Daily sync on blockers and progress
4. Testing after each phase
5. Final verification against acceptance criteria

---
