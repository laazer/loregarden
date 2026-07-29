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
| `edit` | `content`, `target` | Editable text (agent/workflow/gate/text) |
| `calendar` | `view`, optional `events` | Month/week/day calendar |
| `calendar_event` | `event` | Single calendar event |

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

## Do not

- Do not write ticket markdown files — tickets live in the database.
- Do not dump full `TicketDetail` JSON into a fence; the UI fetches live state.
- Do not invent ticket/agent ids. Look them up via MCP first.
