# Writing a finding, and proving it landed

## Append, do not replace

A finding is appended under its own heading so the original text stays intact and
the provenance is legible:

    ## Prior art — <project> (<owner/repo>, <licence>, @<short-sha>), surveyed <date>

Name the licence and the commit. If the repository ships no LICENSE file, say so —
a reader deciding whether to copy code needs that before they read the finding.

## What earns its place

Quote the source when its own words are the finding; a design note that states a
failure mode or a measured number is worth more than your paraphrase of it. Prefer:

- a constant with its justification (`500 chars, because the two longest lines
  measured here are 413 and 406`)
- a stated non-claim (`static differences remain candidates, not causal proof`)
- a dated rejected alternative and the symptom that forced the change

Avoid a feature list, and avoid adopt-vs-build framing. The question is always
what we reimplement on our own components.

Record what you could not establish. A survey that names its limits is reusable;
one that reads as complete gets trusted past its evidence.

## The write path

`loregarden_update_ticket` with the full new description — read the current one
first, append, send the whole thing. Long reports go to
`loregarden_attach_artifact`, never into a response body and **never as a
markdown file in the tree**: the orchestrator sweeps the working tree into
whatever ticket is running, so a findings `.md` lands in an unrelated commit.

A durable lesson about how this control plane behaves goes to
`loregarden_append_learning`. An assumption you had to pick goes to
`loregarden_append_checkpoint`.

## Verify byte-exact

Every write is checked by reading it back:

    got = read(ticket).description
    assert got == original.rstrip() + header + body
    assert got.startswith(original.rstrip())

The second assertion is the one that matters — it proves nothing was replaced.
Report the before and after lengths.

If the MCP tools are unavailable, the HTTP API is the next path; the CLI is a
last resort and **runs migrations against the live database from a worktree**,
which has locked this repository out of writes before. Prefer waiting for the
server over reaching around it.

## After the survey

Add the project to `scratch/ref_projects/ref_gh_links.md` in the main checkout,
with a note if anything about it is unusual (no licence, a product site rather
than a repository, a fork of something else).
