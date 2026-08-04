# CLIENT DESIGN CONTRACT

Enforceable layering rules for `client/**`. For *where things are*, read `AGENTS.md`; for how to
behave, `CLAUDE.md`. This file is a contract — a gate reads it, and a frontend reviewer checks
what the gate cannot.

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
| **L5** | **Frame** | `AppIconRail`, `AppTopbar`, `AppActionBar`, status bar | L0, L1 |
| **L6** | **Pages** | `pages/` | L3–L5 |

`Frame` is the codebase's own word — `AppLayout` already wraps everything in `.app-frame`.

## L1 — GENERIC PRIMITIVES ARE DOMAIN-FREE

A generic primitive knows nothing about loregarden. It may not import `api/client`,
`state/uiStore`, or react-query, and may not name a domain type (Ticket, Run, Agent, Workspace,
Stage, Workflow, Approval).

If `Tabs` knows what a ticket is, the mess has been rebuilt one level down.

Grow this directory by **extraction from real duplicates**, not speculation. `Tabs` and `Modal`
are the first two because eleven and nineteen instances respectively already exist.

## L2 — DOMAIN PRIMITIVES ARE AGENT-ADDRESSABLE

A domain primitive renders one entity an agent can talk about. Four requirements, the first three
gate-checkable:

1. **Wire schema first.** The server type is the source (`server/loregarden/services/chat_primitives/`),
   the TS type mirrors it. A component whose props cannot cross the wire cannot be agent-emitted —
   decide that deliberately, not by accident.
2. **Registered under a stable string kind**, in `PrimitiveKind` and the exhaustive
   `Record<PrimitiveKind, Renderer>`. Exhaustiveness means a missing renderer is a compile error,
   not a blank card.
3. **Versioned, with an unknown-kind fallback.** `CHAT_PRIMITIVES_VERSION` plus
   `UnknownPrimitiveCard`: an older client meeting a newer agent's card degrades to a warning, not
   a crash. Any new registry inherits this.
4. **Every operator affordance has an addressable equivalent.** If a human can close a tab, an
   agent must be able to name that action, or the surface is silently human-only.

## L4 — A CONTAINER MAY NOT KNOW WHAT IT CONTAINS

Containers own identity, ordering, persistence, and lifecycle — open, close, focus, reorder,
restore. They do not own content. **A container module may not import L2 or L3.** Content arrives
by injection: `children`, a render prop, or a registry lookup.

`PrimitiveSlot` is the existing proof. It hosts any of the twenty domain primitives — sizes them,
portals them into a full-viewport overlay — and imports none of them:

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
container owns, adding a surface means editing the container — which is how eleven tab bars
happened.

**Surfaces expose a descriptor; containers render descriptors, never content.** A tab needs a
title, icon, status dot, dirty flag. The surface supplies that as data.

## L5 — THE FRAME IS AGENT-INERT

Navigation, topbar, icon rail, action bar, and status bar render operator state only. No agent
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
`TerminalPrimitive` is a *reference to* a terminal, not a terminal hosting primitives — keep that
distinction explicit or it will erode.

## ENFORCEMENT

| Rule | Gate | Mechanism |
|---|------|-----------|
| Layer import direction | ✅ | path-based import check |
| L1 domain-type ban | ✅ | import + identifier ban |
| L4/L5 may not import L2/L3 | ✅ | path-based import ban |
| L2 mount sites | ✅ | primitive imports banned from L1/L4/L5/terminal modules |
| Registry exhaustiveness | ✅ | `Record<PrimitiveKind, …>` + `tsc -b`, already free |
| Token-only values, no colour literals | ✅ | pairs with the CSS-var gate |
| One implementation per interaction pattern | ⚠️ | review — DRY detection is fuzzy |
| Agent-addressable affordances | ❌ | reviewer judgement |

### Scope: staged files, whole file

The gate runs on **staged files**, and evaluates each one **in full** — not just changed hunks.
Touching a file means bringing it into compliance.

This is deliberate. Diff-scoped checking lets a violation survive indefinitely as long as nobody
edits its exact lines, and a gate that fails on all eleven existing tab implementations at once
blocks every commit. Whole-file-on-touch converts the backlog into steady amortised migration.

### Known violations, to migrate on touch

Eleven tab implementations — `.tab-btn` (Dashboard, QueueSnapshotManager), `.terminal-tab`,
`.studio-subtab`, `.queue-rail-tab`, `.artifacts-subtab`, `.operation-diff-review-tab`,
`.ticket-diff-review-tabs`, `.manager-tabs`, `.code-editor-tab`, `.branch-triage-tab`, `.tab-bar`.

Nineteen `*Modal.tsx`, each hand-rolling `role="dialog"` and a portal.

Five diff viewers — `InlineCodeDiffReview`, `OperationDiffReviewView`, `QueueDiffViewer`,
`TicketDiffReviewPanel`, `BranchTriageDiffPanel`.
