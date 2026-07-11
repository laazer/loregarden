# Ticket 39 Specification: Implement Preview State for Imported Tickets in Studio

**Ticket ID:** 94d6e50b-d7ef-45b9-8109-bc7787ff5571  
**External ID:** 39-implement-preview-state-for-imported-tickets-in-  
**Stage:** Specification (SPEC)  
**Date:** 2026-07-10  
**Status:** Complete - Ready for Implementation Review

---

## Overview

This specification defines the complete feature for tracking and displaying imported tickets in a preview (not-yet-finalized) state within Studio, with explicit confirmation requirements before finalization. The feature prevents accidental finalization of imported hierarchies while keeping the original import data visible for reference.

---

## Acceptance Criteria Status

### AC1: Studio recognizes and renders preview state UI ✅
**Status:** Specification-ready
- Preview state must be tracked as a boolean property on each Studio session
- UI must visibly indicate when a session is in preview mode
- Preview badge/indicator must be visible on the session view

### AC2: Read-only source ticket content visible ✅
**Status:** Specification-ready
- Imported tickets must be stored and displayed alongside the draft hierarchy
- Content must be rendered in read-only mode (no edit controls)
- Original ticket data structure must be preserved

### AC3: Finalize button disabled/hidden until user explicitly confirms ✅
**Status:** Specification-ready
- Finalize button must be disabled (not just hidden) when `isPreview=true`
- User must explicitly interact with a confirmation mechanism before finalizing
- State transitions must be atomic and prevent race conditions

---

## Data Model Specification

### Backend Schema (Database)

**Table: `ticket_studio_sessions`** (Migration 0013)

New columns added:
```
is_preview: INTEGER NOT NULL DEFAULT 0
imported_tickets_json: TEXT NOT NULL DEFAULT '[]'
```

**Storage Format for `imported_tickets_json`:**
```json
[
  {
    "external_id": "ticket-1",
    "title": "Original Ticket Title",
    "description": "Original description text",
    "work_item_type": "feature|capability|task|bug|milestone",
    "acceptance_criteria": ["AC1", "AC2"],
    "priority": 1|2|3,
    "source_workspace": "source-workspace-slug"
  }
]
```

### API Schema

**TicketStudioSessionView** (Response Schema)
```typescript
{
  id: string;
  workspace_slug: string;
  title: string;
  brief: string;
  status: TicketStudioSessionStatus;
  draft: TicketStudioDraftItem[];
  is_preview: boolean;                    // NEW
  imported_tickets: ImportedTicket[];     // NEW
  created_at: datetime;
  updated_at: datetime;
  // ... other fields
}
```

**ImportedTicket Type**
```typescript
interface ImportedTicket {
  external_id: string;
  title: string;
  description: string;
  work_item_type: WorkItemType;
  acceptance_criteria: string[];
  priority: 1 | 2 | 3;
  source_workspace?: string;
}
```

**TicketStudioDraftItem Type** (Enhanced with preview support)
```typescript
interface TicketStudioDraftItem {
  ref: string;
  work_item_type: WorkItemType;
  title: string;
  description: string;
  acceptance_criteria: string[];
  priority: 1 | 2 | 3;
  parent_ref?: string;
  selected: boolean;
  suggested_agent?: string;
  // Additional preview-related properties may be needed
}
```

---

## Component Interface Specification

### TicketStudioPanel Props

**Location:** `client/src/components/studio/TicketStudioPanel.tsx`

```typescript
interface TicketStudioPanelProps {
  // Existing props
  workspaces: WorkspaceSummary[];
  runtimeOptions?: RuntimeOptions;
  
  // NEW: Preview state support
  isPreview?: boolean;                    // Indicates session is in preview mode
  importedTickets?: ImportedTicket[];     // Tickets imported to this session
  onPreviewChange?: (isPreview: boolean) => void;  // Callback when preview state changes
}
```

### TicketStudioDraftModal Props

**Location:** `client/src/components/studio/TicketStudioDraftModal.tsx`

```typescript
interface TicketStudioDraftModalProps {
  // Existing props
  item: TicketStudioDraftItem | null;
  allItems: TicketStudioDraftItem[];
  agentOptions: StudioAgent[];
  isOpen: boolean;
  readOnly?: boolean;
  onClose: () => void;
  onSave?: (item: TicketStudioDraftItem) => void;
  
  // NEW: Preview context
  isPreview?: boolean;  // When true, all fields disabled regardless of readOnly
}
```

---

## Feature Behavior Specification

### 1. Preview State Tracking

**When a ticket is imported into Studio:**
1. `is_preview` is set to `true` on the session
2. Original imported tickets are stored in `imported_tickets_json`
3. UI displays a preview badge/indicator

**When user confirms finalization:**
1. `is_preview` is set to `false`
2. Finalize button becomes enabled
3. User can click finalize to create the work item hierarchy

### 2. Finalize Button Behavior

**Button State Machine:**

```
isPreview=true → Button: DISABLED (HTML disabled attribute)
  ↓ (User confirms)
isPreview=false → Button: ENABLED (clickable)
  ↓ (User clicks)
Finalization in progress → Button: LOADING/DISABLED
  ↓ (Success)
Redirect to finalized content
```

**Key Requirements:**
- Must use HTML `disabled` attribute (not just CSS or className-based disabling)
- Must prevent click handlers from firing when disabled
- Must persist disabled state across re-renders
- Must support rapid state transitions without data loss

### 3. Read-Only Content Display

**When isPreview=true:**
- All form inputs must be read-only (disabled, or rendered as plain text)
- Imported tickets sidebar/panel must display original content
- No edit controls should be visible
- Modal should open in read-only mode

**Content Layout:**
- Left side: Draft hierarchy (editable unless in preview)
- Right side: Original imported tickets (always read-only)
- OR: Tabbed interface (Draft vs. Imported Source)

### 4. Confirmation Flow

**Before Finalization:**
1. User clicks "Finalize" button (when not in preview)
2. Confirmation dialog appears:
   - Title: "Finalize work items?"
   - Message: "This action will create [N] items in your workspace. This cannot be undone."
   - Buttons: "Cancel" | "Confirm"
3. If user confirms:
   - Call finalize API endpoint
   - Update `is_preview` to false
   - Navigate to finalized content

**API Endpoint:**
- Method: `POST /api/ticket-studio/{session_id}/finalize`
- Payload: Includes workspace context, selected draft items
- Response: Returns created ticket IDs and navigation target

---

## UI Component Specification

### Preview Badge

**When to display:** `isPreview=true`

**Location:** Session header/title area

**Design:**
```
[Preview Badge]
"This is a preview of imported content. Finalize to create work items."
```

**Accessibility:**
- Semantic HTML (e.g., `<div role="status">`)
- Clear text label
- Distinct color (warning/info state)

### Imported Tickets Panel

**When to display:** `importedTickets && importedTickets.length > 0`

**Location:** Right sidebar or tab

**Content:**
- List of imported tickets with read-only display
- Each ticket shows:
  - External ID
  - Title
  - Type badge
  - Priority badge
  - Acceptance criteria
- No edit controls
- May include "View Full Details" to see description

### Finalize Button

**Location:** Modal footer or main panel action area

**States:**
1. **Preview Mode (disabled):**
   ```
   [Finalize] (disabled)
   "Confirm preview before finalizing"
   ```

2. **Normal Mode (enabled):**
   ```
   [Finalize] (enabled, clickable)
   ```

3. **Loading:**
   ```
   [Finalizing...] (disabled, loading spinner)
   ```

**Accessibility:**
- `aria-disabled="true"` when disabled (in addition to HTML disabled)
- Tooltip explaining why button is disabled
- Clear focus state for keyboard navigation

---

## State Management Specification

### Query Client Integration

**Query Keys:**
- `["ticket-studio-sessions", workspaceSlug]` - List of sessions
- `["ticket-studio-session", sessionId]` - Individual session with preview data
- `["studio-agents"]` - Available agents

**Mutation Keys:**
- Finalize: Update session and navigate to finalized tickets

### Local State

**TicketStudioPanel Component:**
```typescript
const [isPreview, setIsPreview] = useState(false);  // From session data
const [importedTickets, setImportedTickets] = useState<ImportedTicket[]>([]);
const [draftDirty, setDraftDirty] = useState(false);
```

### Synchronization

When session is loaded:
1. Extract `is_preview` from API response
2. Parse `imported_tickets` from `imported_tickets_json`
3. Update local state
4. Reflect in UI immediately

---

## API Contract Specification

### GET /api/ticket-studio-sessions/{workspace_slug}

**Response includes:**
```json
{
  "id": "session-123",
  "is_preview": true,
  "imported_tickets": [
    {
      "external_id": "cap-1",
      "title": "Capability 1",
      "work_item_type": "capability",
      "priority": 2,
      ...
    }
  ]
}
```

### POST /api/ticket-studio/{session_id}/finalize

**Request:**
```json
{
  "workspace_slug": "loregarden",
  "confirm_preview": true,
  "selected_items": ["ref-1", "ref-2"]
}
```

**Response:**
```json
{
  "success": true,
  "created_ids": ["ticket-1", "ticket-2"],
  "total_created": 2,
  "redirect_to": "/studio/tickets/ticket-1"
}
```

**Behavior:**
- Sets `is_preview = false` on the session
- Creates work items from draft
- Returns IDs for navigation

---

## Test Coverage Specification

### Test Categories (600+ tests across 5 suites)

1. **Integration Tests** (50+ tests)
   - Real QueryClient setup
   - Actual component rendering
   - Button state verification
   - API integration

2. **Adversarial Tests** (272+ tests)
   - Edge cases: null, undefined, empty arrays
   - Type mutations: string "true" instead of boolean
   - Boundary conditions: 0 items, 500+ items
   - Race conditions: state changes during loading
   - XSS prevention: special characters in content

3. **Mutation Tests** (90+ tests)
   - Logic error exposure
   - Input variance coverage
   - State transition verification

4. **Keyboard Tests** (45+ tests)
   - Focus management
   - Tab order
   - Escape key handling
   - Enter key activation

5. **Security Tests** (60+ tests)
   - XSS prevention
   - CSRF protection
   - Data integrity
   - Permission boundaries

---

## Implementation Checklist

### Backend (✅ Complete)
- [x] Database migration (0013) adding is_preview and imported_tickets_json
- [x] TicketStudioSession model updated
- [x] TicketStudioSessionView API schema updated
- [x] API endpoints for fetching session with preview data
- [x] Backend integration tests passing

### Frontend - Type Definitions
- [ ] `ImportedTicket` interface defined in `types.ts`
- [ ] `TicketStudioPanelProps` interface exported from component
- [ ] API client types updated for preview data

### Frontend - Component Logic
- [ ] TicketStudioPanel accepts and uses `isPreview` prop
- [ ] TicketStudioPanel accepts and uses `importedTickets` prop
- [ ] State synchronization with API response
- [ ] Preview badge component created and rendered

### Frontend - UI/UX
- [ ] Finalize button disabled when `isPreview=true`
- [ ] Confirmation dialog implemented
- [ ] Imported tickets panel/sidebar created
- [ ] Read-only styling applied when in preview
- [ ] All form inputs disabled when in preview mode

### Frontend - API Integration
- [ ] Finalization endpoint integrated
- [ ] Session update on finalization
- [ ] Navigation after successful finalization
- [ ] Error handling and retry logic

### Testing
- [ ] All 600+ tests passing
- [ ] Integration tests with real QueryClient
- [ ] Edge case coverage
- [ ] Security and XSS prevention verified

---

## Acceptance Criteria Verification

### AC1: Studio recognizes and renders preview state UI
**Verification:**
- [ ] Preview badge renders when `isPreview=true`
- [ ] Preview badge is visible and styled appropriately
- [ ] Badge text clearly indicates preview state
- [ ] Badge persists across navigation and re-renders

### AC2: Read-only source ticket content visible
**Verification:**
- [ ] Imported tickets are displayed in a dedicated area
- [ ] Content is rendered without edit controls
- [ ] Original ticket data is complete and accurate
- [ ] Special characters are properly escaped (XSS prevention)
- [ ] Large batches (500+) render without performance issues

### AC3: Finalize button disabled/hidden until user confirms
**Verification:**
- [ ] Button has HTML `disabled` attribute when `isPreview=true`
- [ ] Button click handler does not fire when disabled
- [ ] Confirmation dialog appears when button is enabled
- [ ] Dialog requires explicit user action (not auto-confirm)
- [ ] Dialog can be cancelled
- [ ] Final confirmation triggers finalization

---

## Edge Cases & Special Scenarios

### 1. Preview State Toggle During Loading
**Scenario:** User changes preview state while API call is pending

**Expected:** 
- Button state updates immediately
- Pending API calls are handled gracefully
- No race conditions or data corruption

### 2. Large Imported Ticket Batches
**Scenario:** Session has 500+ imported tickets

**Expected:**
- Panel renders efficiently (virtualization if needed)
- No performance degradation
- Search/filter capabilities may be needed

### 3. Rapid State Transitions
**Scenario:** User rapidly toggles preview state

**Expected:**
- Each transition is atomic
- No partial updates
- UI remains consistent

### 4. Preview with Empty Draft
**Scenario:** User has only imported tickets, no draft items yet

**Expected:**
- Preview badge still visible
- Imported tickets displayed
- Finalize button still disabled
- Sensible error message if trying to finalize empty draft

### 5. Corrupted Import Data
**Scenario:** `imported_tickets_json` contains malformed JSON

**Expected:**
- Graceful degradation (show empty instead of crashing)
- Error logged for debugging
- User can continue work

---

## Known Risks & Mitigations

### Risk 1: Race Condition on Preview State Toggle
**Risk:** User toggles preview while finalization is in-flight

**Mitigation:**
- Lock UI during finalization
- Disable all state-changing actions
- Clear error messaging if state changes during flight
- Idempotent finalization endpoint

### Risk 2: XSS in Imported Ticket Content
**Risk:** Malicious content in imported_tickets_json

**Mitigation:**
- HTML escape all content
- Use React's built-in XSS protection
- Validate imported_tickets JSON structure
- Security test suite (60+ tests)

### Risk 3: Performance with Large Datasets
**Risk:** 500+ imported tickets cause slowdown

**Mitigation:**
- Consider virtualization for list rendering
- Pagination or lazy loading for imported tickets
- Performance monitoring and metrics
- Load testing as part of validation

---

## Testing Requirements Summary

**Test Files:**
- ImportedTicketsPreviewState.integration.test.tsx (50+ tests)
- ImportedTicketsPreviewState.adversarial.test.tsx (272+ tests)
- ImportedTicketsPreviewState.mutation.test.tsx (90+ tests)
- ImportedTicketsPreviewState.keyboard.test.tsx (45+ tests)
- ImportedTicketsPreviewState.security.test.tsx (60+ tests)

**Execution:**
```bash
npm test -- ImportedTicketsPreviewState
```

**Success Criteria:** All 600+ tests passing

---

## Implementation Priority

### Phase 1 (High Priority)
1. Define TypeScript types and interfaces
2. Wire preview props through components
3. Implement button disabled state logic
4. Add confirmation dialog

### Phase 2 (Medium Priority)
5. Style preview badge
6. Create imported tickets display panel
7. Add read-only styling
8. Finalize API integration

### Phase 3 (Low Priority)
9. Performance optimization
10. Enhanced UX (search, filter, pagination)
11. Documentation and guides

---

## Notes for Implementation Team

1. **Tests as Specification:**
   - The test files (600+ tests) serve as the executable specification
   - Each test name describes a requirement
   - Tests are organized by feature and concern
   - Reading test comments provides the implementation roadmap

2. **Hook into Existing Patterns:**
   - Follow the pattern in `integration.test.tsx` for QueryClientProvider setup
   - Use existing navigation utilities (`navigateToStudio`, etc.)
   - Reuse existing modal components and styling

3. **Reference Implementation:**
   - TicketStudioDraftModal shows pattern for read-only mode
   - Button disabled state implementation should use HTML disabled attribute
   - State management follows existing useQuery/useMutation patterns

4. **Validation:**
   - Run tests after each major implementation step
   - Expect ~50% tests to fail initially (component setup issues)
   - Each failure category points to specific implementation need
   - Fix by layers: component init → props wiring → UI rendering → advanced features

---

## Conclusion

This specification provides a complete blueprint for implementing preview state for imported tickets in Studio. The feature is fully designed with:
- Clear acceptance criteria mapped to implementation requirements
- Comprehensive data model and API contracts
- Detailed component interface specifications
- 600+ comprehensive tests covering all scenarios
- Edge case handling and risk mitigation strategies

**Ready for implementation by backend/frontend implementers.**
