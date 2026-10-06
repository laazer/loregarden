# Finding the tickets a survey answers

Most findings belong on a ticket that already exists. Creating a new one for
something already scoped is how a backlog becomes unreadable, and a ticket is
cheap to append to and expensive to deduplicate.

## Survey all the workspaces, not just loregarden

Four workspaces share this control plane, and a finding often belongs to a
different one than the project you were handed:

| slug | prefix | holds |
|---|---|---|
| `loregarden` | `lg-` | the control plane itself |
| `lore-eden` | `lor-` | the shared agent harness and UI kit |
| `loremaker` | `lmkr-` | the product built on it |
| `blobert` | `blob-` | a driven workspace |

A UI primitive belongs in `lore-eden`, not `loregarden`. A dispatch protocol
finding may belong in `loremaker`. Check all four before concluding there is no
home for a finding.

## The search

`loregarden_list_tickets` takes `workspace_slug`, `search`, `state` and
`work_item_type`. Search matches title and external id only — **not the
description** — so a theme named only in a body will not surface. For a broad
sweep, pull the open backlog once and filter it yourself on title keywords, then
read candidates with `loregarden_get_ticket`.

Read the real ticket before appending. Descriptions are often empty or stale, and
this repository has a recorded failure mode of agents inventing acceptance
criteria that then steer every later stage.

## Judging the home

The right ticket is the one whose **premise** the finding bears on, not the one
whose title shares a word.

- A design pattern goes on the feature that will implement it.
- An argument about what a milestone is for goes on the milestone.
- A measured correction to a ticket's own claim goes on that ticket, stated as a
  correction.

Check the ticket's state and its parent chain. A ticket inside a milestone whose
whole description is a catch-all is a ticket nobody will read; if the content is
substantial and the home is a dumping ground, create a properly parented ticket
and leave a pointer behind instead.

## When it is genuinely new

Create a ticket when nothing covers the premise — not when nothing shares the
vocabulary. `loregarden_create_ticket` needs a `parent` for anything below a
milestone, and acceptance criteria belong in `acceptance_criteria`, never
appended to the description.

Before filing a gap as a defect, prove it against `origin/main`:

    git fetch -q origin main && git rev-list --count HEAD..origin/main
    git grep -n "<symbol>" origin/main -- 'server/**'

A worktree hundreds of commits behind will show you a bug that was fixed weeks
ago, and the ticket you file on it wastes the next reader's time.
