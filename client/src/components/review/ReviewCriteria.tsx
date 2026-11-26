import type { ReviewCriterion, ReviewTicket } from "../../api/reviewApi";
import { Input } from "../ui/Input";

const JUDGMENT_LABEL: Record<NonNullable<ReviewCriterion["judges"]>, string> = {
  feel: "How it feels",
  look: "How it looks",
  sound: "How it sounds",
  clarity: "Whether it makes sense",
  choice: "Your choice",
  physical: "Needs your hardware or account",
};

/** What kind of judgment is asked for, and why it has to be a person's. */
export function WhyYou({ judges, reason }: { judges: ReviewCriterion["judges"]; reason: string }) {
  if (!judges) return null;
  return (
    <span className="review-muted">
      <span className="review-step-label">Why you</span> {JUDGMENT_LABEL[judges]}. {reason}
    </span>
  );
}

/**
 * "Which of these are mine to answer?"
 *
 * Only a criterion settled as a person's judgment gets a tick box. The rest
 * are folded: answered for, with how, or with no recorded proof — which is
 * missing agent work, so the action it leads to is Reject, not a check by hand.
 */
export function ReviewCriteria({
  ticket,
  saving,
  onToggle,
}: {
  ticket: ReviewTicket;
  saving: boolean;
  onToggle: (criterion: string) => void;
}) {
  const yours = ticket.criteria.filter((c) => c.disposition === "person");
  const answered = ticket.criteria.filter((c) => c.disposition === "met" || c.disposition === "delegated");
  const unproven = ticket.criteria.filter((c) => c.disposition === null);
  const checked = new Set(ticket.checked_criteria);
  return (
    <>
      {yours.length > 0 ? (
        <section className="review-section" aria-label="Criteria for you to judge">
          <h3 className="review-section-title">For you to judge ({yours.length})</h3>
          <ul className="review-criteria">
            {yours.map((criterion, index) => {
              const id = `review-ac-${ticket.id}-${index}`;
              return (
                <li key={criterion.text}>
                  <Input
                    id={id}
                    type="checkbox"
                    checked={checked.has(criterion.text)}
                    disabled={saving}
                    onChange={() => onToggle(criterion.text)}
                  />
                  <label htmlFor={id}>
                    {criterion.ask} <WhyYou judges={criterion.judges} reason={criterion.reason} />
                  </label>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
      {answered.length + unproven.length > 0 ? (
        <details className="review-section">
          <summary className="review-section-title">{foldTitle(answered.length, unproven.length)}</summary>
          <p className="review-muted">
            What the agents build and test to. None of it is yours to check
            {unproven.length > 0 ? "; if one without proof matters, reject and name it" : ""}.
          </p>
          <CriterionList label="Criteria without recorded proof" criteria={unproven} note="No proof recorded" />
          <CriterionList label="Criteria answered for by agents" criteria={answered} note="" />
        </details>
      ) : null}
    </>
  );
}

function foldTitle(answered: number, unproven: number): string {
  const parts = [
    answered > 0 ? `${answered} answered for by agents` : "",
    unproven > 0 ? `${unproven} without recorded proof` : "",
  ].filter(Boolean);
  return `Acceptance criteria: ${parts.join(", ")}`;
}

function CriterionList({ label, criteria, note }: { label: string; criteria: ReviewCriterion[]; note: string }) {
  if (criteria.length === 0) return null;
  return (
    <ul className="review-criteria review-criteria--read" aria-label={label}>
      {criteria.map((criterion) => (
        <li key={criterion.text}>
          <span>{criterion.text}</span>
          <span className="review-muted">{criterion.reason || note}</span>
        </li>
      ))}
    </ul>
  );
}
