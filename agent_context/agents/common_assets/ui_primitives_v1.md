# Chat UI Primitives v1

Emit interactive cards in chat by wrapping JSON in a fenced `loregarden` block.
The frontend renders each card from live control-plane state; keep payloads as
**thin refs**, not snapshots.

## Fence format

````markdown
```loregarden
{"primitive":"<kind>", ...}
```
````

Rules:

- One JSON object per fence. Multiple cards = multiple fences.
- Surrounding markdown prose is fine; it becomes a `text` part.
- Malformed JSON is shown as plain text — never invent fields to "fix" it.
- Prefer refs (`ticket_id`, `agent_id`, `workflow_slug`) over embedded copies of
  ticket/agent/workflow bodies.

## Kinds

| Kind | Required fields | Purpose |
|------|-----------------|---------|
| `text` | `content` | Plain markdown (rarely emitted explicitly) |
| `thinking` | `content` | Collapsed thought bubble |
| `ticket` | `ticket_id` | Ticket card with play/stop |
| `ticket_workflow` | `ticket_id` | Stage timeline for a ticket |
| `parent_ticket` | `ticket_id` | Parent + children + progress |
| `ticket_list` | `ticket_ids` or `parent_ticket_id` | Hierarchy list with play/stop |
| `status_column` | `status`, `ticket_ids` | Tickets sharing one status |
| `kanban` | `ticket_ids` | Columns by status |
| `filterable_kanban` | `ticket_ids`, `filters` | Kanban with status filters |
| `agent` | `agent_id` or `draft` | Agent preview (create/preview only) |
| `workflow` | `workflow_slug` or `draft` | Workflow graph preview |
| `gate` | `ticket_id`+`stage_key` or `draft` | Gate preview |
| `terminal` | `lines` | Read-only command transcript |
| `edit` | `content`, optional `original`, `path` | Proposed edit: active diff + inline comments → chat; `path` opens in Editor |
| `calendar` | `view`, optional `events` | Month/week/day calendar |
| `calendar_event` | `event` | Single calendar event |
| `workspace` | `workspace_slug` | Live workspace summary |
| `todo_list` | `owner`, `items` | Checklist; `owner:"agent"` + title `"Agent execution plan"` for proposed work steps; only `owner:"user"` is user-editable |
| `branch_history` | `workspace_slug`, `branch` | Branch with recent commit history |
| `commit` | `workspace_slug`, `sha` | Live commit detail (`sha` may be `HEAD`) |
| `qa` | `items` | Ticket-Studio-style questions and answers |
| `giphy` | `giphy_id` or Giphy media `url` | Safe animated Giphy reaction |

Optional `title` on most kinds is a display hint only.

## Examples

Ticket:

````markdown
```loregarden
{"primitive":"ticket","ticket_id":"<uuid-or-external-id>"}
```
````

Thinking:

````markdown
```loregarden
{"primitive":"thinking","content":"Checking active blocked tickets first."}
```
````

Terminal transcript:

````markdown
```loregarden
{"primitive":"terminal","title":"pytest","lines":[{"kind":"command","text":"pytest -q"},{"kind":"stdout","text":"12 passed"}]}
```
````

Proposed edit (active diff with inline comments sent to chat):

````markdown
```loregarden
{"primitive":"edit","target":"agent","target_id":"planner","path":"agent_context/agents/1_planner/planner_v1.md","language":"markdown","title":"Tighten Planner scope","original":"You write plans.\n","content":"You write plans.\nYou never write code.\n"}
```
````

Agent execution plan (`todo_list` with `owner:"agent"` — the user cannot check these boxes).
Use this when outlining work you will do; never invent a `ticket` card for unfiled work:

````markdown
```loregarden
{"primitive":"todo_list","owner":"agent","title":"Agent execution plan","items":[{"id":"tests","text":"Run tests","checked":true},{"id":"review","text":"Review the diff","checked":false}]}
```
````

User Q&A:

````markdown
```loregarden
{"primitive":"qa","interactive":true,"items":[{"id":"scope","question":"Which users are in scope?","answer":""}]}
```
````

## Do not

- Do not write ticket markdown files — tickets live in the database.
- Do not dump full `TicketDetail` JSON into a fence; the UI fetches live state.
- Do not invent ticket/agent ids. Look them up via MCP first.
- Do not use arbitrary image URLs for `giphy`; provide a Giphy ID or HTTPS URL
  hosted by `i.giphy.com` or `media*.giphy.com`.
