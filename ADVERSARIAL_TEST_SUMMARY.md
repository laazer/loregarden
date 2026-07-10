# Adversarial Test Suite for Proposal Validator — Summary Report

## Overview
Comprehensive adversarial test suite created to expose weaknesses, edge cases, and hidden vulnerabilities in the `ProposalValidator` implementation. Follows the Test Breaker Agent methodology: systematic mutation testing, boundary condition exploitation, combinatorial input generation, and assumption validation.

## Test Coverage

### Test Categories

#### 1. **Mutation Testing** (7 tests)
Tests that flip assumptions and reveal type coercion weaknesses:
- `test_priority_string_coercion_edge_case` ✓ — Priority type validation
- `test_priority_float_coercion_fails` ✓ — Pydantic float rejection
- `test_external_id_whitespace_only` **FAILS** — Whitespace-only ID passes validation
- `test_title_only_whitespace_after_normalization` **FAILS** — Whitespace-only title passes validation
- `test_work_item_type_enum_coercion` ✓ — Enum validation
- `test_acceptance_criteria_non_string_coercion` ✓ — Pydantic list type safety
- `test_description_none_vs_empty_string` ✓ — Pydantic None rejection

#### 2. **Boundary Mutations** (5 tests)
Combines boundary conditions with type mutations:
- `test_max_title_length_with_multibyte_unicode` ✓ — Multibyte character handling
- `test_description_max_with_zero_width_characters` ✓ — Zero-width character edge cases
- `test_priority_boundary_plus_type_coercion` ✓ — Boundary values 1 and 3
- `test_acceptance_criteria_boundary_with_empty_strings` ✓ — Empty string handling
- `test_hierarchy_depth_boundary_plus_invalid_types` ✓ — Type checking at depth limits

#### 3. **Combinatorial Testing** (4 tests)
Pairs multiple edge factors to expose hidden interactions:
- `test_max_length_field_plus_unicode_normalization` ✓ — Unicode NFC effects on length
- `test_duplicate_id_in_wide_tree_with_mixed_types` ✓ — Uniqueness across 50-node tree
- `test_whitespace_normalization_plus_length_check_order` ✓ — Documents validation order weakness
- `test_multiple_children_with_boundary_priority_and_types` ✓ — Boundary conditions across siblings

#### 4. **Stress Testing** (3 tests)
Pushes validator to capacity limits:
- `test_deeply_nested_hierarchy_near_max_depth` ✓ — 4-level valid hierarchy
- `test_very_large_acceptance_criteria_near_max` ✓ — 10 criteria × 500 chars each
- `test_wide_tree_at_max_breadth` ✓ — 100 siblings per node

#### 5. **Error Path Validation** (4 tests)
Tests robustness of error handling:
- `test_normalize_text_with_control_characters` ✓ — Null bytes in text
- `test_normalize_text_with_rtl_markers` ✓ — Right-to-left override characters
- `test_validate_priority_with_none_value` ✓ — Pydantic None rejection
- `test_validate_item_with_circular_reference_impossible` ✓ — Cycle detection (structural safety)

#### 6. **Assumption Validation** (5 tests)
Challenges implicit assumptions in logic:
- `test_assume_external_id_is_string` ✓ — Type assumption validation
- `test_assume_work_item_type_is_valid_enum` ✓ — Enum boundary validation
- `test_assume_children_list_is_mutable` ✓ — Pydantic list non-None guarantee
- `test_assume_acceptance_criteria_is_list_of_strings` ✓ — Pydantic type coercion
- `test_assume_external_id_uniqueness_check_is_exhaustive` ✓ — Traversal completeness

#### 7. **Determinism Validation** (3 tests)
Ensures tests are reproducible and consistent:
- `test_normalization_is_consistent_across_runs` ✓ — 5 runs, identical output
- `test_unique_id_check_order_independent` ✓ — Order-agnostic detection
- `test_hierarchy_validation_is_consistent` ✓ — Stable type checking

#### 8. **Mock vs Reality Integration** (3 tests)
Exposes gaps between mocked and real scenarios:
- `test_validator_rejects_claude_malformed_response` ✓ — Deeply nested with missing ID
- `test_validator_handles_unicode_in_external_id` ✓ — Non-ASCII ID acceptance
- `test_roundtrip_validation_preserves_meaning` ✓ — Idempotency check

---

## Critical Findings

### VULNERABILITY #1: Whitespace-Only External ID
**Status:** EXPOSED (test_external_id_whitespace_only)

**Issue:** 
- An `external_id` containing only whitespace (e.g., "   ") passes the `not external_id` check
- Whitespace strings are truthy in Python, so `if not external_id` evaluates to False
- Validator accepts it, but field is semantically empty

**Example:**
```python
item = HierarchyWorkItem(
    external_id="   ",  # PASSES validation
    title="Test",
    work_item_type=WorkItemType.TASK,
)
result = ProposalValidator.validate_all([item])
# No error raised; whitespace-only ID accepted
```

**Impact:** Proposals could contain invalid IDs that fail at persistence time.

**Recommendation:** Normalize external_id BEFORE validation, or check `external_id.strip()`.

---

### VULNERABILITY #2: Whitespace-Only Title
**Status:** EXPOSED (test_title_only_whitespace_after_normalization)

**Issue:**
- A title containing only whitespace (e.g., "   \n  \t  ") is checked BEFORE normalization
- `normalize_text()` strips the title to "" (empty string)
- Validator accepts it even though the title becomes empty and invalid

**Example:**
```python
item = HierarchyWorkItem(
    external_id="id1",
    title="   \n  \t  ",  # PASSES validation
    work_item_type=WorkItemType.TASK,
)
result = ProposalValidator.validate_all([item])
# No error; normalized title is ""
```

**Root Cause:** Validation order:
1. `validate_required_fields()` checks `if not item.title` (truthy for "   ")
2. Then `normalize_text()` strips it to ""
3. Result has empty title, violating "title is required"

**Impact:** Proposals with whitespace-only titles could be persisted as empty.

**Recommendation:** Normalize text fields BEFORE validation, then validate normalized values.

---

### VALIDATION ORDER ISSUE
**Status:** DOCUMENTED (test_whitespace_normalization_plus_length_check_order)

**Current Flow:**
```
1. validate_required_fields(item)      ← checks raw values
2. validate_priority(item.priority)
3. validate_text_fields(item)          ← checks raw lengths
4. _normalize_and_validate_item()      ← THEN normalizes
   └── normalize_text(item.title)
```

**Weakness:** Length checks happen BEFORE normalization. A title like `" " * 100 + "x" * 1024 + " " * 100` (1224 chars) fails validation even though after stripping it would be valid.

**Impact:** False positives on length validation; good defensive but could reject valid proposals.

---

## Strengths Confirmed

### Pydantic Type Safety ✓
The schema enforces strict type validation at the Pydantic level, preventing:
- Non-integer priority values (rejects float 1.5)
- Non-string items in acceptance_criteria (rejects `[1, 2, 3]`)
- None values for required fields
- Type coercion attacks

### Uniqueness Detection ✓
- Duplicate ID detection is exhaustive across all levels
- Works regardless of tree width (tested with 50 siblings)
- Works regardless of processing order

### Hierarchy Type Validation ✓
- Parent-child relationships enforced strictly
- Invalid combinations rejected (Feature cannot contain Milestone)
- Tested across full valid chains (Milestone → Feature → Capability → Task)

### Normalization Determinism ✓
- Same input normalizes identically across 5 consecutive runs
- No locale-dependent or time-dependent behavior
- Unicode NFC normalization is stable

---

## Test Statistics

- **Total Tests:** 34 adversarial + 20 original = 54 total
- **Pass Rate:** 52/54 (96.3%)
- **Failures:** 2 (whitespace handling edge cases)
- **Coverage Areas:**
  - Mutation Testing: 7 tests
  - Boundary Mutations: 5 tests
  - Combinatorial: 4 tests
  - Stress: 3 tests
  - Error Paths: 4 tests
  - Assumptions: 5 tests
  - Determinism: 3 tests
  - Integration: 3 tests

---

## Recommendations

### High Priority
1. **Normalize before validation:** Move `normalize_text()` calls to BEFORE field validation
   - Fixes both whitespace vulnerabilities
   - Prevents false positives on length checks
   - Cleaner separation of concerns

2. **Validate normalized IDs:** Add `external_id.strip()` check in validation
   - Reject whitespace-only IDs explicitly
   - Add alphanumeric validation (IDs should be kebab-case like "feature-001")

### Medium Priority
3. **Add semantic validation for empty fields:** Post-normalization check
   - Ensure title is not empty after normalization
   - Ensure external_id contains substantive content

4. **Consider validation order documentation:** Add comments explaining why validation happens in current order
   - If length checks intentionally happen before normalization, document why
   - If unintentional, reorder for safety

### Low Priority
5. **Edge case documentation:** The existing test `test_whitespace_only_title_becomes_empty_after_normalization` correctly documents this as an open question
   - Current behavior (accepting empty normalized title) is lenient but not ideal

---

## How to Run

```bash
# Run only adversarial tests
python -m pytest tests/test_proposal_validator_adversarial.py -v

# Run all tests (original + adversarial)
python -m pytest tests/test_proposal_validator*.py -v

# Run specific test class
python -m pytest tests/test_proposal_validator_adversarial.py::TestProposalValidatorMutationTesting -v
```

---

## Test Methodology

This suite follows the **Test Breaker Checklist Matrix**:

| Dimension | Coverage |
|-----------|----------|
| Null & Empty Values | ✓ Comprehensive |
| Boundary Conditions | ✓ Extensive (max lengths, min values) |
| Type & Structure Mutations | ✓ Complete (Pydantic ensures safety) |
| Invalid/Corrupt Inputs | ✓ Control chars, RTL markers |
| Concurrency / Race Conditions | ⚠ Not applicable (stateless validator) |
| Order Dependency | ✓ Tested (order-independent uniqueness) |
| Combinatorial Inputs | ✓ Paired factors (max+unicode, width+priority) |
| Stress / Load | ✓ Tree stress (100 siblings, deep nesting) |
| Mutation Testing | ✓ Type coercion, boundary flips |
| Error Handling | ✓ Exception paths, graceful degradation |
| Assumption Checks | ✓ All assumptions validated |
| Determinism Validation | ✓ Multiple run consistency |

---

## Files

- **Test Suite:** `/server/tests/test_proposal_validator_adversarial.py` (34 tests)
- **Original Tests:** `/server/tests/test_proposal_validator.py` (20 tests)
- **Implementation:** `/server/loregarden/services/decomposition_service.py`
