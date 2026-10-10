import { useLayoutEffect, useRef } from 'react';
import { MarkdownContent } from './chat/MarkdownContent';
import { draftCriteria, newCriterionDraft, type CriterionDraft } from '../lib/criterionDrafts';

function progressLabel(done: number, total: number): string {
  return `${done} of ${total} done`;
}

export interface TicketCriteriaViewProps {
  criteria: string[];
  checked: string[];
  /** Absent when the ticket cannot be written: the boxes are shown, read-only. */
  onToggle?: (criterion: string, checked: boolean) => void;
  disabled?: boolean;
}

/** Read mode: a checklist whose boxes can be ticked but whose text cannot be edited. */
export function TicketCriteriaView({ criteria, checked, onToggle, disabled = false }: TicketCriteriaViewProps) {
  const done = new Set(checked);
  const doneCount = criteria.filter((c) => done.has(c)).length;

  return (
    <div className="state-card">
      <div className="ticket-ac-heading">
        <div className="state-label">Acceptance Criteria</div>
        {criteria.length > 0 && (
          <span className="ticket-ac-progress">{progressLabel(doneCount, criteria.length)}</span>
        )}
      </div>
      {criteria.length === 0 ? (
        <p className="modal-hint ticket-empty-hint">No acceptance criteria yet. Edit the ticket to add some.</p>
      ) : (
        <ul className="ticket-ac-list" aria-label="Acceptance criteria">
          {criteria.map((criterion, index) => {
            const isChecked = done.has(criterion);
            const id = `ticket-ac-view-${index}`;
            return (
              <li key={`${index}:${criterion}`} className={`ticket-ac-item${isChecked ? ' is-checked' : ''}`}>
                <input
                  id={id}
                  type="checkbox"
                  checked={isChecked}
                  disabled={disabled || !onToggle}
                  onChange={(event) => onToggle?.(criterion, event.target.checked)}
                />
                <label htmlFor={id} className="ticket-ac-text">
                  <MarkdownContent content={criterion} expandable={false} normalize={false} />
                </label>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export interface TicketCriteriaEditorProps {
  rows: CriterionDraft[];
  onChange: (rows: CriterionDraft[]) => void;
  disabled?: boolean;
}

/** Edit mode: every criterion is a text field, and still a checkbox. */
export function TicketCriteriaEditor({ rows, onChange, disabled = false }: TicketCriteriaEditorProps) {
  const inputs = useRef(new Map<number, HTMLTextAreaElement>());
  const criteriaCount = draftCriteria(rows).length;

  // Grow each field to its text: real criteria run past one line, and a
  // single-line input hid the end of most of them.
  useLayoutEffect(() => {
    for (const el of inputs.current.values()) {
      el.style.height = 'auto';
      el.style.height = `${el.scrollHeight}px`;
    }
  }, [rows]);

  const focusRow = (key: number) => {
    // After the render that adds or removes the row.
    window.requestAnimationFrame(() => inputs.current.get(key)?.focus());
  };

  const update = (key: number, patch: Partial<CriterionDraft>) =>
    onChange(rows.map((row) => (row.key === key ? { ...row, ...patch } : row)));

  /** A criterion is one line; pasting several lines adds one criterion per line. */
  const updateText = (index: number, text: string) => {
    const [first, ...more] = text.split(/\r?\n/);
    const row = { ...rows[index], text: first };
    const added = more.filter((line) => line.trim()).map((line) => newCriterionDraft(line));
    onChange([...rows.slice(0, index), row, ...added, ...rows.slice(index + 1)]);
    if (added.length) focusRow(added[added.length - 1].key);
  };

  const insertAfter = (index: number) => {
    const row = newCriterionDraft();
    onChange([...rows.slice(0, index + 1), row, ...rows.slice(index + 1)]);
    focusRow(row.key);
  };

  const remove = (index: number) => {
    const neighbour = rows[index - 1] ?? rows[index + 1];
    onChange(rows.filter((_, i) => i !== index));
    if (neighbour) focusRow(neighbour.key);
  };

  return (
    <div className="state-card">
      <div className="ticket-ac-heading">
        <div className="state-label">Acceptance Criteria</div>
        <span className="ticket-ac-progress">
          {criteriaCount === 1 ? '1 criterion' : `${criteriaCount} criteria`}
        </span>
      </div>
      {rows.length === 0 ? (
        <p className="modal-hint ticket-empty-hint">No acceptance criteria yet.</p>
      ) : (
        <ul className="ticket-ac-list" aria-label="Acceptance criteria">
          {rows.map((row, index) => (
            <li key={row.key} className={`ticket-ac-item ticket-ac-edit-row${row.checked ? ' is-checked' : ''}`}>
              <input
                type="checkbox"
                checked={row.checked}
                disabled={disabled}
                aria-label={`Criterion ${index + 1} done`}
                onChange={(event) => update(row.key, { checked: event.target.checked })}
              />
              <textarea
                rows={1}
                ref={(el) => {
                  if (el) inputs.current.set(row.key, el);
                  else inputs.current.delete(row.key);
                }}
                className="btn-secondary filter-select ticket-ac-input"
                aria-label={`Criterion ${index + 1}`}
                value={row.text}
                disabled={disabled}
                placeholder="Describe a testable outcome…"
                onChange={(event) => updateText(index, event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    insertAfter(index);
                  } else if (event.key === 'Backspace' && row.text === '' && rows.length > 1) {
                    event.preventDefault();
                    remove(index);
                  }
                }}
              />
              <button
                type="button"
                className="icon-close-btn"
                aria-label={`Remove criterion ${index + 1}`}
                title="Remove criterion"
                disabled={disabled}
                onClick={() => remove(index)}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      )}
      <button
        type="button"
        className="btn-secondary btn-compact ticket-ac-add"
        disabled={disabled}
        onClick={() => insertAfter(rows.length - 1)}
      >
        + Add criterion
      </button>
      <p className="modal-hint" style={{ marginTop: 4 }}>Enter adds a criterion below; Backspace on an empty one removes it.</p>
    </div>
  );
}
