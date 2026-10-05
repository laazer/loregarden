---
name: reference-survey
description: Survey an outside project — a repository, a product site, a released app — for patterns worth having, and land every finding on the loregarden tickets it answers. Use when handed a URL and asked what is useful in it, or when a named project should be checked against the backlog. Not for reading this repository's own code, not for choosing a dependency to install, and not for a survey whose output is a document rather than ticket changes.
---

# Reference survey — read someone else's work, land it on ours

A survey that ends in a report has produced nothing. **The deliverable is edits to
tickets**, and a finding nobody can act on is a finding you did not make.

Two rules this rests on. **Findings are patterns to reimplement on our own
components, never adopt-vs-build**; the standing brief is to reuse what our
libraries already have and own what they do not. And **every claim about
loregarden is checked against `origin/main`, never a worktree** — a stale
checkout reports good code as broken and you will file the wrong ticket.

## Route the target first

- **Git repository** — clone `--depth 1` into the scratchpad, never the project tree.
- **Product site or docs** — `loregarden_search_reference` to find the page, then
  `loregarden_fetch_reference` for it. Cached per URL, so a second read is free;
  prefer it over WebFetch.
- **Released app with no source** — read what it publishes: changelog, API docs,
  screenshots in the repo. Say plainly that you could not run it.

Resolve what the user means before working. Never substitute a different project.

## Work in four passes

1. **Prior work.** `loregarden_search_prior_work` on the project's domain. If a
   survey already covered it, extend that record rather than starting over.
2. **Read the source for mechanisms**, not features. A design decision with a
   number or a failure mode behind it is worth more than a feature list. Record
   where you looked, so a negative finding means something.
3. **Match the backlog before writing anything.** Most findings answer a ticket
   that exists. See [references/backlog-match.md](references/backlog-match.md).
4. **Write, then verify byte-exact.** See
   [references/writing-findings.md](references/writing-findings.md).

## Finish honestly

Distinguish **observed, inferred, and unknown** in every claim. Say what you could
not run and what you did not read. A limitation stated once in the append is worth
more than a confident summary.

Before finishing, revisit what you set out to answer and mark each question
answered, partially answered, or unresolved. Do not call a survey complete while
a question you opened is still open.

If the user asks about UI or UX, that is a second pass over surfaces, not a glance
at a screenshot: [references/ui-pass.md](references/ui-pass.md).
