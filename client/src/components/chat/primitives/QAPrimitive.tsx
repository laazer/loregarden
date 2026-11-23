import { useState } from "react";

import { PrimitiveCard } from "./PrimitiveCard";
import type { LaterUserReply, QAItem, QAPart } from "./types";
import { Button } from "../../ui/Button";

function initialAnswers(items: QAItem[]): string[] {
  return items.map((item) => item.answer ?? "");
}

export function QAPrimitive({
  part,
  onSubmit,
  laterUserReply,
}: {
  part: QAPart;
  onSubmit?: (content: string) => void;
  /**
   * The first user message after this card's, supplied by the thread.
   *
   * The card cannot see the message after its own, so it is given the fact
   * rather than inferring one from `part`. Absent or null is the card exactly as
   * it has always been.
   */
  laterUserReply?: LaterUserReply | null;
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
  // Text the operator typed and has not sent. Compared against what
  // `initialAnswers` seeded the state with, not against "": a card that arrived
  // carrying stored answers is not a draft, and comparing against "" would pin
  // every such card open forever.
  const draft = answers.some((answer, index) => answer !== (items[index]?.answer ?? ""));
  // The third reason a card is read-only, beside `part.interactive` and a submit
  // made here: the operator already answered in prose, which is the rule
  // `living_map/records.py` settles the Living Map on. A card the Map records as
  // settled must not still be asking.
  //
  // Zero items keeps its own branch — "Answered" over a card that never asked
  // anything is a new lie — and a draft wins, because the flip must never
  // discard text the operator typed.
  const answeredInThread = items.length > 0 && Boolean(laterUserReply) && !submitted && !draft;
  // A card with no questions is unanswerable, so it must not present itself as
  // something to answer: it used to render "Answer these before continuing"
  // over a Send button that could never enable, with nothing to type into.
  // The server now rejects an empty `items` at parse time; this covers the rows
  // stored before it did.
  const canAnswer =
    items.length > 0 &&
    part.interactive !== false &&
    Boolean(onSubmit) &&
    !submitted &&
    !answeredInThread;
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
      kind="qa"
      tint={canAnswer ? "you" : "mute"}
      // The only card with a coloured edge: it is waiting on you.
      className={canAnswer ? "lg-primitive-card--needs-you" : undefined}
      title={part.title ?? "Questions & answers"}
      // The chip says sent, waiting or empty; the line under the title is the ask.
      subtitle={
        submitted ? null : canAnswer ? part.prompt ?? "Answer these before continuing" : items.length ? "Question and answer review" : null
      }
      status={
        submitted
          ? { tone: "done", label: "Answers sent" }
          : answeredInThread
            // Where the answer came from, which is what distinguishes it from
            // "Answers sent" — a submit made through this card in this session.
            ? { tone: "done", label: "Answered in the thread" }
            : canAnswer
              ? { tone: "you", label: items.length === 1 ? "Needs you" : `Needs you · ${items.length}` }
              : items.length
                ? null
                : { tone: "wait", label: "Nothing to answer" }
      }
      footerHint={canAnswer ? "Answers go back into this chat" : null}
      actions={
        canAnswer ? (
          <Button variant="primary" compact disabled={!complete} onClick={submit}>
            {items.length === 1 ? "Send answer" : `Send ${items.length} answers`}
          </Button>
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
              ) : answers[index]?.trim() ? (
                <span className="lg-primitive-qa-answer">{answers[index].trim()}</span>
              ) : answeredInThread ? (
                // The per-item "Not answered" placeholder is the other half of
                // the same lie: one reply is not an answer to question 2
                // specifically, but neither did the operator leave it unanswered.
                // The whole card's answer is the one block below.
                null
              ) : (
                <span className="lg-primitive-qa-answer">Not answered</span>
              )}
            </label>
          ))}
        </div>
      ) : (
        <p className="lg-primitive-card-sub">
          This card carried no questions, so there is nothing to answer — the
          agent emitted a malformed question card. Its reply text is unchanged
          above; reply in the thread to keep going.
        </p>
      )}
      {answeredInThread && laterUserReply ? (
        // One block for the whole card, read after the questions. In full and
        // wrapped: a clamp or a "show more" would be the same lie in a new
        // place, hiding what the operator said.
        <div className="lg-primitive-qa-reply">
          <span className="lg-primitive-qa-reply-label">Your reply</span>
          <p className="lg-primitive-qa-reply-text">
            {laterUserReply.text.trim() || "The reply that followed carried no text."}
          </p>
        </div>
      ) : null}
    </PrimitiveCard>
  );
}
