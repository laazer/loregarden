# CLIENT DESIGN CONTRACT

Enforceable layering rules for `client/**`. For *where things are*, read `AGENTS.md`; for how to
behave, `CLAUDE.md`. This file is a contract for a layer gate that has not been built yet. Until
it is, a frontend reviewer checks all of it; the *Enforcement* table below says which rules
another gate already covers.

It exists because the backend has five architecture gates on every commit and the client had one
(oxlint, two rules). The result was eleven independent tab implementations and nineteen
hand-rolled modals. Rules without a gate are taste, and taste does not survive a growing codebase.

## THE LAYERS

Dependencies point **downward only**. A layer may import from layers below it, never above, never
sideways into a peer's internals.

| L | Layer | Lives in | May import |
|---|-------|----------|------------|
| **L0** | **Tokens** | `index.css` custom properties, spring tokens | — |
| **L1** | **Generic primitives** | `components/ui/` | L0 |
| **L2** | **Domain primitives** | `components/chat/primitives/` | L0, L1, one wire schema |
| **L3** | **Surfaces** | chat thread, canvas, diff view, terminal pane, editor pane, ticket detail | L0–L2 |
| **L4** | **Containers** | tab bars, splits, panes, docks, modals, the document model | L0, L1 |
| **L5** | **Frame** | `AppSidebar`, `AppTopbar`, `AppActionBar`, status bar | L0, L1 |
| **L6** | **Pages** | `pages/` | L3–L5 |

`Frame` is the codebase's own word: `AppLayout` already wraps everything in `.app-frame`.

## L1 — GENERIC PRIMITIVES ARE DOMAIN-FREE

A generic primitive knows nothing about loregarden. It may not import `api/client`,
`state/uiStore`, or react-query, and may not name a domain type (Ticket, Run, Agent, Workspace,
Stage, Workflow, Approval).

If `Tabs` knows what a ticket is, the mess has been rebuilt one level down.

Grow this directory by **extraction from real duplicates**, not speculation. `Tabs` and `Modal`
were picked first because eleven and nineteen instances respectively already existed. The modal
has landed as `ModalShell`; there is no shared `Tabs` yet.

## L2 — DOMAIN PRIMITIVES ARE AGENT-ADDRESSABLE

A domain primitive renders one entity an agent can talk about. Four requirements, the first three
gate-checkable:

1. **Wire schema first.** The server type is the source
   (`server/loregarden/models/domain/chat_primitives.py`), and the TS type mirrors it. A component
   whose props cannot cross the wire cannot be agent-emitted, so decide that deliberately.
2. **Registered under a stable string kind**, in `PrimitiveKind` and the exhaustive
   `Record<PrimitiveKind, Renderer>`. Exhaustiveness turns a missing renderer into a compile
   error instead of a blank card.
3. **Versioned, with an unknown-kind fallback.** `CHAT_PRIMITIVES_VERSION` plus
   `UnknownPrimitiveCard`: an older client meeting a newer agent's card degrades to a warning
   instead of crashing. Any new registry inherits this.
4. **Every operator affordance has an addressable equivalent.** If a human can close a tab, an
   agent must be able to name that action, or the surface is human-only without anyone deciding
   it should be.

## L4 — A CONTAINER MAY NOT KNOW WHAT IT CONTAINS

Containers own identity, ordering, persistence, and lifecycle (open, close, focus, reorder,
restore). They do not own content. **A container module may not import L2 or L3.** Content arrives
by injection: `children`, a render prop, or a registry lookup.

`PrimitiveSlot` is the existing proof. It hosts any of the 23 kinds in `PrimitiveKind`, sizing
them and portaling them into a full-viewport overlay, and imports none of them:

```tsx
export function PrimitiveSlot({ kind, children }: { kind: string; children: ReactNode })
```

Domain content renders inside containers constantly. The dependency edge points the other way.

| A container may | A container may not |
|---|---|
| hold an opaque `SurfaceRef` (`kind` an **open** string, registered by surfaces) | import L2/L3 modules |
| render descriptors surfaces supply (title, icon, status, dirty flag) | import `api/client`, or fetch domain data |
| mount injected `ReactNode` children | branch on `kind` to choose a component |
| resolve a renderer through a surface registry | own a closed union of content kinds |

`kind` staying an **open string** is load-bearing. The moment it becomes a closed union the
container owns, adding a surface means editing the container, which is how eleven tab bars
happened.

**Surfaces expose a descriptor; containers render descriptors, never content.** A tab needs a
title, icon, status dot, dirty flag. The surface supplies that as data.

## L5 — THE FRAME IS AGENT-INERT

Navigation, topbar, sidebar, action bar, and status bar render operator state only. No agent
injects into navigation. This is the boundary that keeps "Baxter surfaces one thing into the bar"
from becoming "any agent renders anything anywhere".

## CONTAINMENT

Where an **L2 domain primitive** may be mounted:

| Host | | |
|---|---|---|
| Chat thread, canvas, pane body *(L3)* | ✅ | its purpose |
| Modal body *(L3, inside an L4 shell)* | ✅ | |
| **Containers *(L4)*** | ❌ | containers render descriptors, not content |
| **Frame *(L5)*** | ❌ | agent-inert |
| **Terminal** | ❌ | a leaf |
| **Generic primitives *(L1)*** | ❌ | inverts the layering |

**The terminal is a leaf.** It renders a byte stream; nothing composes inside it.
`TerminalPrimitive` is a *reference to* a terminal, not a terminal hosting primitives. Keep that
distinction explicit or it will erode.

## ENFORCEMENT

| Rule | Gate | Mechanism |
|---|------|-----------|
| Layer import direction | planned | path-based import check (layer gate, not built) |
| L1 domain-type ban | planned | import + identifier ban (layer gate, not built) |
| L4/L5 may not import L2/L3 | planned | path-based import ban (layer gate, not built) |
| L2 mount sites | planned | primitive imports banned from L1/L4/L5/terminal modules (layer gate, not built) |
| Registry exhaustiveness | ✅ | `Record<PrimitiveKind, …>` + `tsc -b`, already free |
| Token-only values, no colour literals | ✅ | the `ts-theme` gate (`ts_theme_check.cjs`), on changed lines |
| One implementation per interaction pattern | ⚠️ | review, since DRY detection is fuzzy |
| Agent-addressable affordances | ❌ | reviewer judgement |

### Scope: staged files, whole file

The layer gate is meant to run on **staged files** and evaluate each one **in full**, including
lines the commit did not change. Touching a file means bringing it into compliance.

This is deliberate. Diff-scoped checking lets a violation survive indefinitely as long as nobody
edits its exact lines, and a gate that fails on all eleven existing tab implementations at once
blocks every commit. Whole-file-on-touch converts the backlog into steady amortised migration.

### Known violations, to migrate on touch

Eight tab implementations: `.tab-btn` (Dashboard), `.terminal-tab`, `.studio-subtab`,
`.operation-diff-review-tab`, `.ticket-diff-review-tabs`, `.code-editor-tab`,
`.branch-triage-tab`, `.tab-bar`.

One `*Modal.tsx`, `TicketDetailsModal`, still hand-rolls `role="dialog"`; the rest go through
`ModalShell`.

Five diff viewers: `InlineCodeDiffReview`, `OperationDiffReviewView`, `QueueDiffViewer`,
`TicketDiffReviewPanel`, `BranchTriageDiffPanel`.
