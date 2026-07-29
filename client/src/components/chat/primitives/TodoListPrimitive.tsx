import { useState } from "react";

import { PrimitiveCard } from "./PrimitiveCard";
import type { TodoItem, TodoListPart } from "./types";

function normalizedItems(items: TodoItem[]): Required<TodoItem>[] {
  return items.map((item) => ({ ...item, checked: Boolean(item.checked) }));
}

function itemSignature(items: TodoItem[]): string {
  return JSON.stringify(items);
}

export function TodoListPrimitive({ part }: { part: TodoListPart }) {
  const owner = part.owner ?? "agent";
  const source = part.items ?? [];
  const signature = itemSignature(source);
  // Keyed on the payload's contents rather than its object identity, so a
  // re-render of the surrounding thread cannot discard the user's ticks.
  const [state, setState] = useState(() => ({
    signature,
    items: normalizedItems(source),
  }));
  if (state.signature !== signature) {
    setState({ signature, items: normalizedItems(source) });
  }
  const items = state.items;
  const setItems = (next: (current: Required<TodoItem>[]) => Required<TodoItem>[]) =>
    setState((current) => ({ ...current, items: next(current.items) }));

  const completed = items.filter((item) => item.checked).length;
  const percent = items.length ? Math.round((completed / items.length) * 100) : 0;
  const userOwned = owner === "user";

  return (
    <PrimitiveCard
      title={part.title ?? (userOwned ? "Your checklist" : "Agent todo list")}
      subtitle={
        userOwned
          ? "You control this checklist"
          : "Updated by the agent as work completes"
      }
      tone={completed === items.length && items.length ? "ok" : "default"}
      meta={
        <>
          <span>{completed}/{items.length} complete</span>
          <span>{owner} owned</span>
        </>
      }
    >
      <div className="lg-primitive-progress" aria-label={`Progress ${percent}%`}>
        <span style={{ width: `${percent}%` }} />
      </div>
      {items.length ? (
        <ul className="lg-primitive-todo-list">
          {items.map((item) => (
            <li key={item.id} className={item.checked ? "is-checked" : undefined}>
              <label>
                <input
                  type="checkbox"
                  checked={item.checked}
                  disabled={!userOwned}
                  onChange={() => {
                    if (!userOwned) return;
                    setItems((current) =>
                      current.map((candidate) =>
                        candidate.id === item.id
                          ? { ...candidate, checked: !candidate.checked }
                          : candidate,
                      ),
                    );
                  }}
                />
                <span>{item.text}</span>
              </label>
            </li>
          ))}
        </ul>
      ) : (
        <p className="lg-primitive-card-sub">No todo items yet.</p>
      )}
    </PrimitiveCard>
  );
}
