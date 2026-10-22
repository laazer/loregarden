## Summary

## User-facing surfaces
<!-- Required when the PR touches client/src/pages or client/src/components. The
     "PR UX section" check fails without it. See CLAUDE.md "What it is for".
     Look at the change on real data first: `task sandbox`, or `task sandbox -- --seeded`
     where the live database is out of reach. -->
- **Question:** <what the operator asks this surface, in one sentence>
- **Action:** <the link, button or filter they use next>
- **Real data:** <what the sandbox showed, and which (snapshot or seeded): counts, the largest case; attach a screenshot>

<!-- For a change nobody can see (refactor, test-only), replace the three above with:
- **No user-visible change:** <why>
-->

<!-- Fix coverage: required when the title is a fix (`fix:`, `fix(scope):`); the
     "PR fix coverage" check fails without it. Uncomment and fill in. See CLAUDE.md
     "Fixing a bad state".

## Fix coverage
- **Existing:** <how rows already in the state were found and repaired, or the query showing there are none, with its count>
- **Re-entry:** <the invariant in server/loregarden/services/invariant_registry.py, in backticks, or `no detector: <reason>` (browser-only state has none)>
- **Prevention:** <the test proving the state cannot be entered: path/to/test_file.py::test_name, or for jest client/src/.../__tests__/file.test.ts::"its exact literal name">
- **Learning:** <why the state was reachable, as a rule the next change can use>

For a fix that leaves no state behind (a crash, a label, a typo), instead:
- **Not a state bug:** <why>
-->

## Verification
