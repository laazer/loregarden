# Research Specification: Finalize Confirmation and Work-Item Creation (42)

## Executive Summary

This endpoint must atomically create a hierarchy of work items from an edited structure, with guaranteed parent-child relationship integrity and all-or-nothing semantics. Research shows the pattern: **insert parents first within an explicit transaction, flush before commit to catch constraint violations, rollback on error**.

---

## Research Summary: Transactional Hierarchical Bulk Insert

### Sources Consulted

1. **SQLAlchemy Documentation: Transactions** — Tier 1 (Engine Authority)
   - Why: SQLModel (used by Loregarden) inherits SQLAlchemy session semantics; this is the canonical reference for transaction behavior.

2. **Martin Fowler: Patterns of Enterprise Application Architecture** — Tier 2 (Technical Implementation)
   - Why: Covers Unit of Work and Repository patterns essential to atomic multi-entity operations.

3. **Game Programming Patterns: State, Observer** — Tier 2 (Technical Implementation)
   - Why: Hierarchical state creation is analogous to game entity hierarchies; patterns apply here.

4. **Loregarden Codebase (`ticket_service.py`, `bulk_queue_operations.py`)** — Tier 1 (Authority in context)
   - Why: Existing ticket creation and bulk operations provide proven patterns and constraints.

---

## Findings

### 1. **Atomic Transaction Scope**
- **Finding**: All work items in the hierarchy must be created within a single database transaction (BEGIN ... COMMIT or ROLLBACK).
- **Source**: SQLAlchemy docs on transaction semantics; confirmed in Loregarden's `bulk_queue_operations.py` which uses explicit `session.rollback()` on error.
- **Confidence**: High
- **Why**: Referential integrity (parent_ticket_id foreign key) + business logic (if parent fails, children must not exist) require all-or-nothing.

### 2. **Parent-First Insertion Order**
- **Finding**: Parents must be inserted before children because of foreign key constraints. Attempting to insert a child before its parent violates `tickets.parent_ticket_id` foreign key.
- **Source**: Loregarden schema (`tables.py`, line 95): `parent_ticket_id: str | None = Field(default=None, foreign_key="tickets.id")`. Standard relational DB constraint enforcement.
- **Confidence**: High
- **Why**: The database enforces referential integrity; this is not a recommendation but a requirement.

### 3. **Flush vs. Commit Pattern**
- **Finding**: Use `session.flush()` after inserting all entities, before `session.commit()`. Flush detects constraint violations without committing; commit finalizes. On flush error: `session.rollback()` discards all pending changes.
- **Source**: SQLAlchemy docs: "flush() is implicit before query operations; explicit flush() catches constraint violations early."
- **Confidence**: High
- **Applicability**: Loregarden uses SQLModel (thin SQLAlchemy wrapper), so this pattern applies directly.
- **Code pattern**:
  ```python
  try:
      session.flush()  # Detect FK, unique key, not-null violations
      session.commit()  # Finalize
  except Exception as e:
      session.rollback()  # Discard all pending changes
      raise
  ```

### 4. **External ID Uniqueness Under Concurrency**
- **Finding**: External ID must be unique per workspace. Loregarden's `ticket_service.py` (lines 61–78) uses a thread lock (`_external_id_lock`) to serialize ID generation, preventing duplicate IDs during concurrent creates.
- **Source**: Loregarden codebase; common pattern for ID generation safety.
- **Confidence**: High
- **Implication**: If the finalize endpoint auto-generates external IDs, the same locking strategy is required.

### 5. **Error Reporting**
- **Finding**: On transaction failure, return a clear message including:
  - Which work item (title/external_id) caused the failure
  - The constraint violated (e.g., "Duplicate external_id", "Parent not found", "Invalid work_item_type")
  - Confirmation that rollback occurred (no partial inserts)
- **Source**: Martin Fowler's enterprise patterns; Loregarden's practice in `bulk_queue_operations.py`.
- **Confidence**: Medium (UX best practice, not a strict technical requirement)

### 6. **Parent-Child Type Validation**
- **Finding**: Not all parent-child type combinations are valid. Loregarden's hierarchy model enforces:
  - Milestone (top-level, no parent)
  - Epic (child of Milestone)
  - Capability (child of Epic)
  - Task (child of Capability)
  - Subtask (child of Task)
- **Source**: `hierarchy_service.py::validate_parent_child()` and `ticket_service.py::_create_ticket_impl()` (lines 137–146).
- **Confidence**: High
- **Implication**: The finalize endpoint must validate parent-child type pairs before insertion.

### 7. **Return Value**
- **Finding**: Success response should include the created ticket IDs (in insertion order: parent first, then children). This allows the client to map the edited hierarchy to persisted IDs.
- **Source**: Common in REST APIs handling bulk creates; Loregarden's async/queue patterns suggest need for mapping.
- **Confidence**: Medium (inferred from acceptance criteria)

---

## Gaps

1. **Hierarchy Depth Limit**: No authoritative source consulted on whether there's a recommended max depth for the hierarchy. Likely a design decision (e.g., prevent trees >5 levels deep to avoid N+1 query issues).

2. **Concurrent Edits & Optimistic Locking**: If the hierarchy is edited while another user is finalizing, no source consulted on conflict resolution strategy (last-write-wins, abort, or merge).

3. **Workflow State on Creation**: Unclear whether newly created work items should inherit the parent's workflow state, start in "backlog", or use a default. Depends on Loregarden's domain rules.

---

## Recommended Specialists

- **Backend Implementer** (apply_patch skill)
  - Will implement the endpoint, transaction logic, and parent-child validation.
  - Should cross-reference `ticket_service.py::create_ticket()` and `bulk_queue_operations.py` for patterns.

- **Test Designer** (test_design skill)
  - Will design test cases covering:
    - Happy path: 3-level hierarchy (parent → epic → capability → tasks)
    - FK constraint violation (non-existent parent)
    - Duplicate external_id within hierarchy
    - Type validation (invalid parent-child pair)
    - Rollback confirmation (no partial inserts on failure)

- **Test Breaker** (test_break skill)
  - Will attempt to find edge cases: massive hierarchies, special characters in titles, concurrent requests.

---

## Specification Details

### Endpoint Contract

**POST /api/tickets/finalize-hierarchy**

#### Request Payload

```json
{
  "workspace_slug": "loregarden",
  "hierarchy": [
    {
      "external_id": "41-finalization",
      "title": "Finalization and Work-Item Persistence",
      "work_item_type": "epic",
      "description": "Create endpoint/handler for bulk work-item creation",
      "acceptance_criteria": ["Endpoint accepts hierarchy", "Creates atomically"],
      "priority": 1,
      "children": [
        {
          "external_id": "42-finalize-confirmation",
          "title": "Implement finalize confirmation and work-item creation",
          "work_item_type": "capability",
          "children": [
            {
              "external_id": "42-backend-endpoint",
              "title": "Backend endpoint implementation",
              "work_item_type": "task",
              "children": []
            }
          ]
        }
      ]
    }
  ]
}
```

#### Response on Success

```json
{
  "created_ids": [
    "work-item-uuid-1",  // Parent (Epic)
    "work-item-uuid-2",  // Child 1 (Capability)
    "work-item-uuid-3"   // Child 2 (Task)
  ],
  "total_created": 3
}
```

#### Response on Failure

```json
{
  "error": "Referential integrity violation",
  "detail": "Work item 'Create async handler' (type: task): parent not found (ID: <invalid-uuid>)",
  "rolled_back": true
}
```

---

## Implementation Checklist (for next stage)

- [ ] Validate all work-item types against the hierarchy rules (see `hierarchy_service.py`)
- [ ] Check for duplicate external_ids within the hierarchy
- [ ] Validate parent_ticket_id references (parent must exist in workspace before child insert)
- [ ] Use explicit transaction: `try: flush() → commit(); except: rollback() → raise`
- [ ] Test with 1-level, 3-level, and 5-level hierarchies
- [ ] Test rollback on FK violation (simulate by inserting child before parent in a separate test)
- [ ] Ensure external_id generation does not race (use same locking as `_next_external_id`)
- [ ] Return created IDs in insertion order (parent first)

---

## References

- SQLAlchemy Transactions: https://docs.sqlalchemy.org/en/20/core/connections.html#transaction-basics
- Martin Fowler: Unit of Work Pattern: https://martinfowler.com/eaaCatalog/unitOfWork.html
- Loregarden ticket_service.py: `server/loregarden/services/ticket_service.py`
- Loregarden bulk_queue_operations.py: `server/loregarden/api/bulk_queue_operations.py`
- Loregarden hierarchy_service.py: `server/loregarden/services/hierarchy_service.py`
