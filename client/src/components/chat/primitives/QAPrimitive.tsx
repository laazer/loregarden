import { useState } from "react";

import { PrimitiveCard } from "./PrimitiveCard";
import type { QAItem, QAPart } from "./types";

function initialAnswers(items: QAItem[]): string[] {
  return items.map((item) => item.answer ?? "");
}

export function QAPrimitive({
  part,
  onSubmit,
}: {
  part: QAPart;
  onSubmit?: (content: string) => void;
}) {
  const items = part.items ?? [];
  const signature = JSON.stringify(items);
  // Keyed on the questions themselves: a re-render of the surrounding thread
  // must not wipe answers the user has typed but not yet sent.
  const [state, setState] = useState(() => ({
    signature,
    answers: initialAnswers(items),
    submitted: false,
  }));
  if (state.signature !== signature) {
    setState({ signature, answers: initialAnswers(items), submitted: false });
  }
  const { answers, submitted } = state;
  const setAnswers = (next: (current: string[]) => string[]) =>
    setState((current) => ({ ...current, answers: next(current.answers) }));
  const canAnswer = part.interactive !== false && Boolean(onSubmit) && !submitted;
  const complete = items.length > 0 && items.every((_, index) => answers[index]?.trim());

  const submit = () => {
    if (!onSubmit || !complete) return;
    const content = items
      .map(
        (item, index) =>
          `${index + 1}. ${item.question}\nAnswer: ${answers[index].trim()}`,
      )
      .join("\n\n");
    onSubmit(content);
    setState((current) => ({ ...current, submitted: true }));
  };

  return (
    <PrimitiveCard
      title={part.title ?? "Questions & answers"}
      subtitle={
        submitted
          ? "Answers sent"
          : canAnswer
            ? part.prompt ?? "Answer these before continuing"
            : "Question and answer review"
      }
      tone={submitted ? "ok" : canAnswer ? "accent" : "default"}
      meta={<span>{items.length} questions</span>}
      actions={
        canAnswer ? (
          <button
            type="button"
            className="lg-primitive-run-btn lg-primitive-run-btn--confirm"
            disabled={!complete}
            onClick={submit}
          >
            Send answers
          </button>
        ) : null
      }
    >
      {items.length ? (
        <div className="lg-primitive-qa-list">
          {items.map((item, index) => (
            <label key={item.id} className="lg-primitive-qa-item">
              <span className="lg-primitive-qa-question">
                <span>{index + 1}</span>
                {item.question}
              </span>
              {canAnswer ? (
                <textarea
                  value={answers[index] ?? ""}
                  rows={2}
                  placeholder="Your answer…"
                  aria-label={item.question}
                  onChange={(event) =>
                    setAnswers((current) => {
                      const next = [...current];
                      next[index] = event.target.value;
                      return next;
                    })
                  }
                />
              ) : (
                <span className="lg-primitive-qa-answer">
                  {answers[index]?.trim() || "Not answered"}
                </span>
              )}
            </label>
          ))}
        </div>
      ) : (
        <p className="lg-primitive-card-sub">No questions supplied.</p>
      )}
    </PrimitiveCard>
  );
}
