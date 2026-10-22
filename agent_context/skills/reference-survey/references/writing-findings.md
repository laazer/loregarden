# Writing a finding, and proving it landed

## A finding goes in the ticket's document, not its description

Every finding on a ticket lives in one document on that ticket: title
**"Prior art — reference survey"**, kind `finding`. The description keeps the
spec and a pointer to the document, nothing else. Surveys used to append their
blocks to the description; by October 2026 that was 84% of the bytes on 304
tickets and the spec could no longer be found under them, so they were moved
off (`lg-reference-sources-1007`). Do not put them back.

Each finding is one block in that document, under its own heading, so the
provenance is legible:

    ## Prior art — <project> (<owner/repo>, <licence>, @<short-sha>), surveyed <date>

Name the licence and the commit. If the repository ships no LICENSE file, say so —
a reader deciding whether to copy code needs that before they read the finding.

A **correction to the ticket's own claim** (a number it states that the live
data contradicts, say) is not prior art. It is spec: it goes in the description,
under its own heading that does not start with `## Prior art`.

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

Two writes per ticket, document first.

1. **Append to the document.** Writing the same ticket + kind + title stores a new
   version and the newest wins, so a write replaces the whole body: read the
   current one, add your block, send all of it.

       loregarden_list_artifacts ticket_id=<external_id> kind=finding include_ancestors=false
       # the row titled "Prior art — reference survey", if any; without
       # include_ancestors=false the parent's same-titled document is listed too
       loregarden_read_artifact artifact_id=<id>      # body at content.body
       loregarden_write_document ticket_id=<external_id> kind=finding \
           title="Prior art — reference survey" body=<old body + "\n\n" + block> \
           summary="<n> prior-art blocks"

   No document yet: the body is your block alone.

2. **Point the description at it** with `loregarden_update_ticket` and the full
   description: read it, edit, send the whole thing. Exactly one pointer section,
   at the end of the spec (or where the blocks stood, if spec follows them), under
   the header
   `## Prior art — moved to a document, <date>` (keep that wording: it is what
   the check for stray inline prior art searches for). It names the document
   title, the newest artifact id, and every block heading in the document, so a
   reader knows what is there without opening it. If a pointer already exists,
   update its artifact id and add your heading to its list; do not add a second
   one. The pointers written on 2026-10-14 are the format; `lg-hive-workplace-78`
   is one.

`loregarden_write_document` needs no orchestration run. `loregarden_attach_artifact`
requires a `run_id`, so a survey run from a terminal cannot use it. Never write a
findings markdown file into the tree: the orchestrator sweeps the working tree
into whatever ticket is running, so a findings `.md` lands in an unrelated commit.

A durable lesson about how this control plane behaves goes to
`loregarden_append_learning`. An assumption you had to pick goes to
`loregarden_append_checkpoint`.

## Verify byte-exact

Read both writes back. The document store strips exactly one trailing newline
and nothing else, so compare against the stripped body or every check fails:

    got = read_artifact(new_id).content.body
    assert got == sent_body.rstrip("\n")
    assert got.startswith(old_body)          # nothing earlier was replaced

    desc = get_ticket(ticket).description
    assert desc.startswith(spec_before_pointer)   # the spec is untouched

The `startswith` assertions are the ones that matter — they prove nothing was
replaced. Report the document's length before and after.

If the MCP tools are unavailable (the server is down), the same tools run
in-process with no server: `./scripts/loregarden-cli.sh mcp call
loregarden_write_document ticket_id=<external_id> body=@<file> …`. If the CLI
reports an unknown tool, the checkout's build predates it: update the checkout
rather than reaching around it. Name the ticket by its `external_id`; a bare
`ticket_number` exists once per workspace and is refused. A build behind the
database's migrations is refused too. **Never write the ticket with raw
SQLite**: a hand-written `WHERE ticket_number = 137` once rewrote a ticket in a
workspace nobody meant to touch, stamped in local time.

## The write path, as one call

`loregarden_survey_write` performs both writes above, adds the criteria through
the safe path, and does the read-back for you; prefer it from a survey pass. The
steps above remain the method when writing by hand.

## After the survey

Finish the pass (`loregarden_survey_pass action=finish`): outcome, where it
stopped, and the unread paths worth a later pass. The record replaces
`scratch/ref_projects/`; do not add to those files.
