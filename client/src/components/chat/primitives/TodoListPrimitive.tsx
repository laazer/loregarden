import { useState } from "react";

import { PrimitiveCard } from "./PrimitiveCard";
import { PlayButton } from "./RunControlButton";
import type { TodoItem, TodoListPart } from "./types";

function normalizedItems(items: TodoItem[]): Required<TodoItem>[] {
  return items.map((item) => ({ ...item, checked: Boolean(item.checked) }));
}

function itemSignature(items: TodoItem[]): string {
  return JSON.stringify(items);
}

/** User message the Run button posts so Baxter executes the plan in-chat. */
export function agentPlanExecuteMessage(
  title: string | null | undefined,
  items: Required<TodoItem>[],
): string {
  const heading = title?.trim() || "Agent execution plan";
  const lines = items.map((item) => {
    const mark = item.checked ? "x" : " ";
    return `- [${mark}] ${item.text} (id: ${item.id})`;
  });
  return [
    // Keep in sync with AGENT_PLAN_EXECUTE_PREFIX in baxter_chat_service.py —
    // the server grants write access on the selected adapter for this prefix.
    `Execute this agent execution plan now. Complete each unchecked step using tools.`,
    `As you finish steps, re-emit the same todo_list with checked:true on completed items.`,
    "",
    `Plan: ${heading}`,
    ...lines,
  ].join("\n");
}

export function TodoListPrimitive({
  part,
  onSubmit,
}: {
  part: TodoListPart;
  onSubmit?: (content: string) => void;
}) {
  const owner = part.owner ?? "agent";
  const source = part.items ?? [];
  const signature = itemSignature(source);
  // Keyed on the payload's contents rather than its object identity, so a
  // re-render of the surrounding thread cannot discard the user's ticks.
  const [state, setState] = useState(() => ({
    signature,
    items: normalizedItems(source),
    started: false,
  }));
  if (state.signature !== signature) {
    setState({ signature, items: normalizedItems(source), started: false });
  }
  const items = state.items;
  const setItems = (next: (current: Required<TodoItem>[]) => Required<TodoItem>[]) =>
    setState((current) => ({ ...current, items: next(current.items) }));

  const completed = items.filter((item) => item.checked).length;
  const percent = items.length ? Math.round((completed / items.length) * 100) : 0;
  const userOwned = owner === "user";
  const remaining = items.some((item) => !item.checked);
  const canRun =
    !userOwned && Boolean(onSubmit) && remaining && items.length > 0 && !state.started;
  const allDone = items.length > 0 && !remaining;

  const run = () => {
    if (!onSubmit || !canRun) return;
    onSubmit(agentPlanExecuteMessage(part.title, items));
    setState((current) => ({ ...current, started: true }));
  };

  return (
    <PrimitiveCard
      title={part.title ?? (userOwned ? "Your checklist" : "Agent todo list")}
      subtitle={
        state.started
          ? "Execution requested"
          : userOwned
            ? "You control this checklist"
            : canRun
              ? "Press Run to execute this plan in chat"
              : allDone
                ? "All steps complete"
                : "Updated by the agent as work completes"
      }
      tone={allDone ? "ok" : canRun || state.started ? "accent" : "default"}
      meta={
        <>
          <span>
            {completed}/{items.length} complete
          </span>
          <span>{owner} owned</span>
        </>
      }
      actions={canRun ? <PlayButton onClick={run} /> : null}
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
