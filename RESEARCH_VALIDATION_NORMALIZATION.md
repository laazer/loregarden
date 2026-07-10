# Research Summary: Validation and Normalization of Decomposition Proposals

**Ticket:** 37-validate-and-normalize-decomposition-proposals  
**Stage:** Specification  
**Researcher:** Blobert (Research Librarian)  
**Date:** 2026-07-10

---

## Executive Summary

Decomposition proposals from the Claude-powered DecompositionService must be validated and normalized before persistence. This research identifies three critical areas:

1. **Required Field Validation** — Pydantic already enforces; document as boundary enforcement
2. **Hierarchy Structure Validation** — Three constraints (valid types, no orphans, no cycles)
3. **Text Field Normalization** — Encoding, length limits, whitespace handling

---

## 1. Required Field Validation

### Current State
All `HierarchyWorkItem` objects enforce these fields via Pydantic:
- `external_id` (str, required)
- `title` (str, required)
- `work_item_type` (WorkItemType enum: milestone|feature|capability|task|bug)
- `description` (str, optional, defaults to "")
- `acceptance_criteria` (list[str], optional, defaults to [])
- `priority` (int, optional, defaults to 3)
- `parent_ticket_id` (str | None, optional, not set during proposal phase)
- `children` (list[HierarchyWorkItem], optional, defaults to [])

### Sources
- **Tier 1:** Code inspection of `loregarden/models/domain/schemas.py:HierarchyWorkItem`
- **Tier 1:** Pydantic validation already enforced during `_parse_item()` in decomposition_service.py

### Recommendation
**No changes needed.** Pydantic validation at parsing boundary is sufficient. Document that proposals are guaranteed to have all required fields before entering persistence flow.

**Risk Mitigation:** Add defensive check at finalization boundary to catch any edge cases where proposals bypass parsing (e.g., direct API POST).

---

## 2. Hierarchy Structure Validation

### Constraint 1: Valid Parent-Child Relationships

**Defined by VALID_HIERARCHY enum** (loregarden/models/domain/enums.py):
```
milestone   → can contain: [feature, bug]
feature     → can contain: [capability, bug]
capability  → can contain: [task, bug]
task        → cannot contain children
bug         → cannot contain children
```

**Source:** Tier 1 — Machine-enforced rule defined in codebase

**Implementation:** Recursive validation during tree traversal
```python
def validate_hierarchy_types(item: HierarchyWorkItem) -> None:
    allowed = VALID_HIERARCHY.get(item.work_item_type, [])
    for child in item.children:
        if child.work_item_type not in allowed:
            raise ValueError(f"{item.work_item_type} cannot contain {child.work_item_type}")
        validate_hierarchy_types(child)
```

**Test Coverage:** decomposition_service.py tests show valid chains at lines 165-210, 803-854

---

### Constraint 2: No Orphaned Nodes

**Definition:** All non-root items must have a valid parent existing in the hierarchy tree.

**Problem:** LLM might generate items with parent references that don't exist in the response.

**Algorithm:**
```python
def validate_no_orphans(hierarchy: list[HierarchyWorkItem]) -> None:
    all_ids = set()
    def collect_ids(item):
        all_ids.add(item.external_id)
        for child in item.children:
            collect_ids(child)
    
    for root in hierarchy:
        collect_ids(root)
    
    # Verify all parent references exist (if stored)
    # Note: during proposal phase, parent_ticket_id is None; this validates tree structure only
```

**Confidence:** High (Tier 1 — structural property of tree)

**Test Coverage:** Tests show multiple root items accepted (line 573-602); orphan detection not explicitly tested

---

### Constraint 3: No Cyclic References

**Definition:** Cannot have A → B → ... → A paths in hierarchy.

**Likelihood:** Low for LLM-generated output, but possible with adversarial input.

**Algorithm:** Depth-first search with visited-set
```python
def validate_no_cycles(hierarchy: list[HierarchyWorkItem]) -> None:
    visited = set()
    rec_stack = set()
    
    def has_cycle(item):
        visited.add(item.external_id)
        rec_stack.add(item.external_id)
        for child in item.children:
            if child.external_id not in visited:
                if has_cycle(child):
                    return True
            elif child.external_id in rec_stack:
                return True
        rec_stack.remove(item.external_id)
        return False
    
    for root in hierarchy:
        if has_cycle(root):
            raise ValueError("Cyclic reference detected in hierarchy")
```

**Confidence:** Medium (edge case; not tested in current test suite)

**Note:** Can only occur if external_id references are allowed as children (current design uses nested objects, preventing this)

---

## 3. Text Field Normalization

### 3a. Encoding & Unicode Normalization

**Standard:** Unicode Normalization Form NFC (Composed)
- **Source:** Tier 2 — [Unicode TR15: Unicode Normalization Forms](https://unicode.org/reports/tr15/)
- **Why:** Prevents comparison issues with equivalent but differently-composed characters (e.g., "ä" vs "a◌̈")

**Python Implementation:**
```python
import unicodedata

def normalize_text(text: str) -> str:
    return unicodedata.normalize('NFC', text)
```

**When to Apply:** 
- Title (always)
- Description (always)
- Each acceptance criterion (always)

**Test Coverage:** Test at line 411-429 shows special characters are preserved; no normalization needed for basic safety

---

### 3b. Length Limits

**Current Observations from Test Suite:**
| Field | Max Tested | Recommendation | Rationale |
|-------|-----------|-----------------|-----------|
| Title | 10,000 chars | **max 1,024** | UI display, search index, URL parameters |
| Description | 5,000 chars | **max 10,000** | LLM bounded by token limit (~4k output); leave margin |
| Acceptance Criteria (list) | 10 items | **max 10 items** | Practical UX limit; too many ACs indicate over-scoping |
| Each AC | Not tested | **max 500 chars** | Single criterion should be concise |
| Priority | 1-3 | **enforce range** | Currently accepts 0, -5, 99999 — should validate [1,3] |

**Sources:**
- Tier 3 — Boundary mutation tests at lines 1311-1329 (title length)
- Tier 3 — Edge case tests at lines 389-409 (description length)
- Tier 2 — UX convention (typical form field constraints)

---

### 3c. Whitespace Normalization

**Current Behavior:** No normalization in codebase

**Recommendation:**
1. **Titles:** `text.strip()` — remove leading/trailing whitespace
2. **Descriptions:** 
   - `text.strip()` — remove leading/trailing
   - Preserve intentional line breaks but collapse 3+ consecutive `\n` → `\n\n`
3. **Acceptance Criteria:** `text.strip()` for each item

**Pattern:**
```python
def normalize_whitespace(text: str, preserve_breaks: bool = False) -> str:
    text = text.strip()
    if preserve_breaks:
        # Collapse multiple newlines to max 2
        import re
        text = re.sub(r'\n\n+', '\n\n', text)
    return text
```

**Confidence:** Medium (best practice; not critical for correctness)

---

### 3d. Special Characters & Injection Prevention

**Finding:** Test suite at lines 1103-1131, 1331-1349 shows safe handling of:
- SQL injection attempts in external_id
- Path traversal sequences
- HTML/script injection in descriptions
- Template injection attempts

**Conclusion:** **No sanitization needed at validation layer.** Character preservation is intentional; sanitization belongs at display/render layer.

**Example:** `<script>alert('xss')</script>` stored safely in database; output encoding handles display safety

---

## 4. External ID Uniqueness

**Constraint:** External IDs must be unique within a single proposal response.

**Why:** Prevents ambiguity when mapping to database IDs during finalization.

**Algorithm:**
```python
def validate_external_id_uniqueness(hierarchy: list[HierarchyWorkItem]) -> None:
    seen_ids = set()
    
    def check_ids(item):
        if item.external_id in seen_ids:
            raise ValueError(f"Duplicate external_id: {item.external_id}")
        seen_ids.add(item.external_id)
        for child in item.children:
            check_ids(child)
    
    for root in hierarchy:
        check_ids(root)
```

**Test Coverage:** Lines 947-983 show positive test for uniqueness; line 1443-1467 show code doesn't detect duplicates

---

## 5. Priority Validation

**Constraint:** Priority ∈ {1, 2, 3}
- 1 = high
- 2 = medium
- 3 = low

**Current Bug:** Tests at lines 1132-1213 show code accepts:
- Priority 0 (outside range)
- Priority -5 (negative)
- Priority 99999 (outside range)

**Recommendation:** Add explicit validation:
```python
def validate_priority(priority: int) -> None:
    if not (1 <= priority <= 3):
        raise ValueError(f"Priority must be 1-3, got {priority}")
```

---

## 6. Depth and Breadth Limits

**Current State:** No limits documented or enforced

**Observations from Tests:**
- **Depth:** Tested up to 100+ levels (lines 1414-1441) — **recommend max 10 levels**
- **Breadth:** Tested up to 1000 siblings (lines 1386-1412) — **recommend max 100 children per node**

**Rationale:**
- **Depth:** UI tree rendering becomes impractical beyond 5-10 levels; execution complexity
- **Breadth:** Database query performance; UI navigation UX

**Implementation:**
```python
def validate_tree_limits(item: HierarchyWorkItem, depth: int = 0, max_depth: int = 10, max_breadth: int = 100) -> None:
    if depth > max_depth:
        raise ValueError(f"Hierarchy exceeds max depth {max_depth}")
    if len(item.children) > max_breadth:
        raise ValueError(f"Item {item.external_id} has {len(item.children)} children; max {max_breadth}")
    for child in item.children:
        validate_tree_limits(child, depth + 1, max_depth, max_breadth)
```

---

## Implementation Checklist for Ticket 37

### Phase 1: Create ProposalValidator Service

**File:** `loregarden/services/proposal_validator.py`

Methods to implement:
- [ ] `validate_required_fields(item: HierarchyWorkItem)` — Already enforced by Pydantic; add defensive check
- [ ] `validate_hierarchy_structure(hierarchy: list[HierarchyWorkItem])` — Check VALID_HIERARCHY rules
- [ ] `validate_external_id_uniqueness(hierarchy)` — Detect duplicates
- [ ] `validate_no_orphans(hierarchy)` — All non-roots have parent in tree
- [ ] `validate_no_cycles(hierarchy)` — Detect cyclic references
- [ ] `validate_tree_limits(hierarchy)` — Check depth/breadth limits
- [ ] `validate_priority(priority)` — Ensure [1, 2, 3]
- [ ] `validate_text_fields(item)` — Check lengths, normalize encoding/whitespace
- [ ] `validate_all(hierarchy)` — Master validation orchestrator

### Phase 2: Define Constraints

Add to domain or configuration:
- [ ] Title max length: **1,024 chars**
- [ ] Description max length: **10,000 chars**
- [ ] Acceptance criteria: **max 10 items, each max 500 chars**
- [ ] Priority range: **[1, 2, 3]**
- [ ] Max hierarchy depth: **10 levels**
- [ ] Max children per node: **100**

### Phase 3: Text Normalization

Implement in proposal validator:
- [ ] Unicode NFC normalization for all text fields
- [ ] Whitespace stripping (title, description, AC items)
- [ ] Newline collapsing for descriptions (3+ → 2)
- [ ] Encoding validation (implicit via JSON parsing)

### Phase 4: Integration

- [ ] Call `ProposalValidator.validate_all()` in DecompositionService after LLM parsing
- [ ] Call validator again in finalize-hierarchy API endpoint before database insertion
- [ ] Return detailed validation errors to API consumer
- [ ] Log validation failures for monitoring

---

## References

**Tier 1 — Authoritative Sources**
- [Pydantic Documentation](https://docs.pydantic.dev) — Data validation framework
- [JSON Schema Specification](https://json-schema.org) — Structural validation standard
- [Unicode Standard TR15](https://unicode.org/reports/tr15/) — Text normalization

**Tier 2 — Implementation Patterns**
- [Martin Fowler — API Design](https://martinfowler.com) — Validation at boundaries
- [Game Programming Patterns](https://gameprogrammingpatterns.com) — Constraint checking

**Tier 3 — Project Context**
- `loregarden/models/domain/enums.py:VALID_HIERARCHY` — Hierarchy rules
- `loregarden/services/decomposition_service.py` — LLM integration
- `loregarden/services/hierarchy_service.py` — Existing validation patterns
- `tests/test_decomposition_service.py` — Comprehensive edge cases

---

## Gaps & Questions for Implementation

1. Should duplicate external IDs across different proposal generations be allowed? (Currently only within-generation checked)
2. Are the suggested depth/breadth limits appropriate for the use case? (10/100 conservative recommendations)
3. Should validation be strict (fail fast) or permissive (warn + normalize)? (Recommend strict)
4. Should acceptance criteria be required non-empty list, or optional empty list?
5. Are there performance implications for validating very large hierarchies (e.g., 1000 nodes)?

---

**Status:** ✅ Research complete — Ready for implementation agent
