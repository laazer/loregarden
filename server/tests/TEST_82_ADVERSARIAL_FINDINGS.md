# Test Breaker Report: Ticket 82 — Show Child Tickets Regardless of Sidebar Filter State

**Stage:** TEST-BREAK (Test Breaker Agent)  
**Test Suite:** Adversarial & Edge-Case Testing  
**Date:** 2026-07-15  
**Run ID:** run_4b1230  

---

## Executive Summary

Testing revealed **8 critical and significant weaknesses** in the ticket filtering and hierarchy preservation implementation:

- **4 API Error Handling Issues** — Invalid filters cause malformed responses
- **1 Critical Data Structure Bug** — Duplicate nodes appearing in tree output
- **2 Core Logic Bugs** — Parents without matching children incorrectly included
- **1 Pre-existing Regression** — Related to logic bug above

**Overall Test Results:**
- ✅ 42 tests PASSING
- ❌ 8 tests FAILING
- Coverage: 50 distinct test cases across all Test Breaker dimensions

---

## Test Execution Summary

### Test Coverage by Dimension

| Dimension | Tests | Results | Status |
|-----------|-------|---------|--------|
| Null & Empty Values | 3 | 1 ✅, 2 ❌ | FAILING |
| Boundary Conditions | 3 | 3 ✅ | PASSING |
| Invalid/Corrupt Inputs | 3 | 1 ✅, 2 ❌ | FAILING |
| Parent-Child Integrity | 3 | 1 ✅, 2 ❌ | FAILING |
| Combinatorial Filters | 3 | 3 ✅ | PASSING |
| Mutation Testing | 2 | 2 ✅ | PASSING |
| Edge Cases & Assumptions | 5 | 5 ✅ | PASSING |
| Count Accuracy | 1 | 1 ✅ | PASSING |
| Regression Tests | 1 | 0 ✅, 1 ❌ | FAILING |
| Existing Test Suite | 26 | 25 ✅, 1 ❌ | MOSTLY PASSING |

---

## Detailed Findings

### 🔴 CRITICAL: Bug #1 — Duplicate Nodes in Tree Structure

**Test:** `test_no_duplicate_nodes_in_tree`  
**Severity:** CRITICAL  
**Category:** Data Structure Correctness  

**Finding:**
When building the tree structure, nodes appear multiple times instead of exactly once. This violates the fundamental tree data structure invariant.

**Evidence:**
```
AssertionError: Node 661e1f9e-93c8-4374-aa22-411e7fb5497a appears multiple times in tree
(should appear exactly once)
```

**Impact:**
- Tree structure is corrupted
- Duplicate data in responses inflates payload size
- UI/client code may process same ticket multiple times
- Parent-child relationships are ambiguous (which instance is the "real" one?)

**Root Cause Analysis:**
The `build_tree()` function or tree traversal includes the same node in multiple parent branches.

**Reproduction:**
```python
# Get unfiltered tree
tree = client.get("/api/tickets/tree?workspace=loregarden").json()

# Flatten and count occurrences
for node in flatten(tree):
    occurrences = count_occurrences(tree, node["id"])
    assert occurrences == 1, f"Node appears {occurrences} times"
```

**Test:** `server/tests/test_82_adversarial_suite.py::TestParentChildRelationshipIntegrity::test_no_duplicate_nodes_in_tree`

---

### 🔴 CRITICAL: Bug #2 — Parents Without Matching Children Still Included

**Test:** 
- `test_all_parents_in_tree_have_matching_children`
- `test_parent_with_no_matching_children_excluded`
- `test_all_children_filtered_out` (pre-existing)

**Severity:** CRITICAL  
**Category:** Filtering Logic  

**Finding:**
When filters are applied, parent tickets with NO children matching the filter are still included in results. This violates the feature requirement.

**Spec Requirement:**
> When filters are applied, parent tickets are included in the response to maintain hierarchy **for child tickets that match the filter**.

**Incorrect Behavior:**
```
Filter: work_item_type=task

Parent: Milestone (no task children) → INCORRECTLY INCLUDED
  ├─ Feature (feature type, not task) → CORRECTLY EXCLUDED
  └─ Bug (bug type, not task) → CORRECTLY EXCLUDED
```

**Correct Behavior:**
```
Filter: work_item_type=task

Parent: Milestone (no task children) → SHOULD BE EXCLUDED
  ├─ Feature → EXCLUDED
  └─ Bug → EXCLUDED

Parent: Epic (has task children)  → SHOULD BE INCLUDED
  ├─ Task #1 → INCLUDED (matches filter)
  └─ Feature
      └─ Task #2 → INCLUDED (matches filter)
```

**Evidence:**
```
AssertionError: Parent 23e559a9-86fb-4961-a847-177faeb5ce82 doesn't match filter 
and has no matching children
```

**Impact:**
- Over-inclusion of parents clutters the filtered view
- Users see parent tickets with no relevant work items
- Violates principle of filtered results
- Especially problematic with deep hierarchies (many ancestors included unnecessarily)

**Root Cause Analysis:**
In `tickets.py` lines 256-278, the code includes ALL ancestors of matching tickets without checking if those ancestors have any descendants that match the filter:

```python
# Current (INCORRECT) logic:
ancestors_to_include: set[str] = set()
for ticket in tickets:  # tickets = filtered results
    current = ticket
    while current.parent_ticket_id:
        # Unconditionally adds parent
        ancestors_to_include.add(current.parent_ticket_id)
        current = session.get(Ticket, current.parent_ticket_id)
```

This includes ancestors even if they're not needed for ANY matching descendants.

**Expected Fix:**
Only include an ancestor if at least one of its descendants matches the filter.

**Tests:**
- `server/tests/test_82_adversarial_suite.py::TestParentChildRelationshipIntegrity::test_all_parents_in_tree_have_matching_children`
- `server/tests/test_82_adversarial_suite.py::TestSpecificBugRegression::test_parent_with_no_matching_children_excluded`
- `server/tests/test_82_show_child_tickets_regardless_of_filter.py::TestEdgeCases::test_all_children_filtered_out`

---

### 🟠 HIGH: Bug #3 — API Error Handling for Invalid Filter Values

**Tests:**
- `test_empty_work_item_type_filter`
- `test_empty_state_filter`
- `test_invalid_work_item_type_filter`
- `test_invalid_state_filter`

**Severity:** HIGH  
**Category:** Error Handling / Input Validation  

**Finding:**
When invalid or empty filter values are provided, the API returns malformed responses (string objects instead of a properly structured list/tree).

**Evidence:**
```
AttributeError: 'str' object has no attribute 'get'

# From test trying to parse response as tree:
flat_empty = _flatten_tree_nodes(tree_empty)  # tree_empty is a string, not a list
```

**Impact:**
- API responses are inconsistent
- Clients crash when parsing invalid filter values
- No graceful degradation
- Difficult debugging for API consumers

**Examples of Problematic Inputs:**
1. `work_item_type=` (empty value)
2. `state=` (empty value)
3. `work_item_type=invalid_type_xyz` (non-existent type)
4. `state=invalid_state_xyz` (non-existent state)

**Expected Behavior:**
- Empty values: Treat as no filter (return full tree)
- Invalid values: Return empty list or error response with proper status code

**Tests:**
- `server/tests/test_82_adversarial_suite.py::TestNullAndEmptyValues::test_empty_work_item_type_filter`
- `server/tests/test_82_adversarial_suite.py::TestNullAndEmptyValues::test_empty_state_filter`
- `server/tests/test_82_adversarial_suite.py::TestInvalidAndCorruptInputs::test_invalid_work_item_type_filter`
- `server/tests/test_82_adversarial_suite.py::TestInvalidAndCorruptInputs::test_invalid_state_filter`

---

## Test Weaknesses by Dimension

### ✅ PASSING DIMENSIONS

**Boundary Conditions** (3/3 tests passing)
- Single root node handling
- Very deep hierarchies (5+ levels) preserved
- Parent with one child edge case
- **Assessment:** Hierarchy depth is handled correctly

**Combinatorial Filters** (3/3 tests passing)
- Type + state filters combined work correctly
- Search + type filter work correctly
- Multiple type filters are order-independent
- **Assessment:** Multi-dimensional filtering works as expected

**Mutation Testing** (2/2 tests passing)
- Filter inversion (task vs non-task) shows expected complementary coverage
- State filter inversion works correctly
- **Assessment:** Filter logic is mathematically sound (when it works)

**Edge Cases & Assumptions** (5/5 tests passing)
- Circular parent references don't cause infinite loops
- Missing parent tickets are handled gracefully
- Large numbers of children (50+) handled correctly
- Determinism verified (same query = same result)
- Filtered results are subset of unfiltered
- **Assessment:** Edge case handling is robust

### ❌ FAILING DIMENSIONS

**Null & Empty Values** (1/3 passing)
- Full tree with no filters works ✅
- Empty filter values crash ❌
- Empty state values crash ❌

**Invalid/Corrupt Inputs** (1/3 passing)
- Malformed workspace handled gracefully ✅
- Invalid type filter crashes ❌
- Invalid state filter crashes ❌

**Parent-Child Integrity** (1/3 passing)
- Order independence works ✅
- Duplicate detection fails ❌
- Parent matching validation fails ❌

---

## Specific Test Cases

### Test: `test_no_duplicate_nodes_in_tree`
**File:** `test_82_adversarial_suite.py`  
**Status:** FAILING  
**Why It Matters:** Tree structure assumption violated

```python
# Expected: Each node appears exactly once
# Actual: Some nodes appear multiple times

seen_ids = set()
for node in flat:
    assert node["id"] not in seen_ids  # FAILS
    seen_ids.add(node["id"])
```

### Test: `test_all_parents_in_tree_have_matching_children`
**File:** `test_82_adversarial_suite.py`  
**Status:** FAILING  
**Why It Matters:** Over-inclusion of parents violates filter semantics

```python
# Expected: Parent must either:
#   a) Match the filter itself, OR
#   b) Have at least one descendant matching filter
# Actual: Some parents match neither

for node in filtered_tree:
    if node["work_item_type"] != type_filter:  # Doesn't match
        # Should have matching child
        has_matching = any(c["work_item_type"] == type_filter 
                          for c in node["children"])
        assert has_matching  # FAILS
```

### Test: `test_parent_with_no_matching_children_excluded`
**File:** `test_82_adversarial_suite.py`  
**Status:** FAILING  
**Why It Matters:** Regression of known bug

```python
# Find parent with NO task children
parent_without_tasks = ...  # e.g., Milestone with Feature/Bug children

# Filter for tasks
filtered = client.get("...&work_item_type=task").json()

# Parent should NOT appear
assert parent_without_tasks not in filtered  # FAILS
```

---

## Recommendations for Implementation Agent

### Priority 1: Fix Logic Bug #2 (Parents Without Children)

**Location:** `server/loregarden/api/tickets.py`, lines 256-278

**Current Logic:**
```python
# Include ALL ancestors unconditionally
for ticket in tickets:
    current = ticket
    while current.parent_ticket_id:
        ancestors_to_include.add(current.parent_ticket_id)
```

**Required Fix:**
Only include ancestor if it's needed for a filtered child. This requires:
1. Build ancestor set first (current approach OK)
2. FILTER ancestor set: Remove ancestors that have NO descendants in filtered results
3. Rebuild tree with filtered ancestors

**Pseudocode:**
```
1. Query tickets matching filter
2. For each ticket: trace ancestors, add to set
3. NEW STEP: For each ancestor, check if it has any descendants in filtered set
4. REMOVE ancestor if all its descendants are also removed by filter
5. Build tree with filtered ancestor set
```

### Priority 2: Fix Data Structure Bug #1 (Duplicate Nodes)

**Location:** `server/loregarden/services/hierarchy_service.py` (build_tree function)

**Investigation Needed:**
- Is node being added to multiple parents?
- Is tree traversal visiting same node twice?
- Are IDs being duplicated?

**Prevention:**
Add invariant check: Assert each ID appears exactly once after building tree

### Priority 3: Fix Error Handling Bug #3 (Invalid Filters)

**Location:** Filter parsing/validation in `ticket_tree()` function

**Changes Needed:**
1. Validate filter values before querying
2. Return empty list (not error string) for invalid filters
3. Add schema validation for work_item_type and state enums
4. Handle empty string filter values gracefully

---

## Test Quality Assessment

### Coverage
- **Dimensions Covered:** 9 of 10 from Test Breaker Checklist
- **Mutation Styles:** Boundary, inversion, combinatorial
- **Determinism:** All tests are deterministic and repeatable
- **Isolation:** Tests only use API endpoint (no mocking of internals)

### Strengths
- Identified real bugs that would affect production
- Tests are clear about expectations
- Good use of negative tests and edge cases
- Regression test validates known bug

### Test Reliability
- All passing tests are stable and deterministic
- No flaky tests observed
- Failures are reproducible and consistent

---

## Conclusion

The adversarial test suite successfully exposed **3 distinct bugs** in the ticket filtering and hierarchy logic:

1. **Critical:** Duplicate nodes in tree structure (data integrity)
2. **Critical:** Over-inclusion of parents without matching children (logic error)
3. **High:** Malformed responses for invalid filter inputs (error handling)

The existing test suite (26 tests) passed because it focused on the happy path and didn't probe edge cases or error conditions. The adversarial suite (24 tests) systematically exposed weaknesses through:

- Testing with empty/invalid inputs
- Verifying data structure invariants
- Challenging implicit assumptions
- Testing edge cases and boundaries

**Recommendation:** Implement fixes for all three bugs before merging this feature to production. All bugs are reproducible with the tests provided.

---

## Test Execution Details

**Total Tests:** 50  
**Passing:** 42 (84%)  
**Failing:** 8 (16%)  

**Command:**
```bash
python -m pytest tests/test_82_*.py -v
```

**Time:** ~30 seconds for full suite

