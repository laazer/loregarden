/**
 * The acceptance-criteria editor against the cap (lg-milestone-that-2250).
 *
 * The question the surface answers: standing at the editor, can the operator
 * tell "may I add another criterion, or is this a second ticket?" *before* they
 * save? Measured read-only on the live database 2026-11-05: 357 of 2,733
 * tickets sit at exactly 12 criteria — 13%, one click from a refusal — so at the
 * cap the most likely experience of this change is pressing a button that looks
 * available. 78 tickets are already over 12 and the largest holds 62, so the
 * editor has to stay fully usable above the cap too.
 */
import { fireEvent, render, screen } from '@testing-library/react';

import { findUsabilityProblems } from '../../lib/usabilityCheck';

import { TicketCriteriaEditor } from '../TicketCriteriaChecklist';
import {
  MAX_ACCEPTANCE_CRITERIA,
  criteriaDrafts,
  draftCriteria,
  type CriterionDraft,
} from '../../lib/criterionDrafts';

function drafts(n: number): CriterionDraft[] {
  return criteriaDrafts(
    Array.from({ length: n }, (_, i) => `criterion ${i}`),
    [],
  );
}

/**
 * The editor is controlled, so the harness owns the rows and re-renders.
 *
 * `storedCount` defaults to what the ticket would already hold if this draft
 * were its saved state — the *normalized* list, not the row count, because that
 * is what the column holds and what the server compares against.
 */
function renderEditor(initial: CriterionDraft[], storedCount = draftCriteria(initial).length) {
  const onChange = jest.fn();
  const view = render(
    <TicketCriteriaEditor rows={initial} onChange={onChange} storedCount={storedCount} />,
  );
  const rerender = (rows: CriterionDraft[], stored = storedCount) =>
    view.rerender(<TicketCriteriaEditor rows={rows} onChange={onChange} storedCount={stored} />);
  return { onChange, rerender, container: view.container };
}

const addButton = () => screen.getByRole('button', { name: /add criterion/i });

it('the readout counts the draft against the cap', () => {
  const { rerender } = renderEditor(drafts(3));
  const readout = screen.getByText(new RegExp(`3 of ${MAX_ACCEPTANCE_CRITERIA}`));
  // Adding or removing a row has to be announced, not just repainted.
  expect(readout).toHaveAttribute('aria-live', 'polite');

  rerender(drafts(0), 0);
  expect(screen.getByText(new RegExp(`0 of ${MAX_ACCEPTANCE_CRITERIA}`))).toBeInTheDocument();
  expect(screen.getByText(/no acceptance criteria yet/i)).toBeInTheDocument();

  rerender(drafts(MAX_ACCEPTANCE_CRITERIA + 2), MAX_ACCEPTANCE_CRITERIA + 2);
  expect(screen.getByText(/over the cap/i)).toBeInTheDocument();
});

it('Add goes disabled at the cap with a reason named by aria-describedby', () => {
  renderEditor(drafts(MAX_ACCEPTANCE_CRITERIA));
  const add = addButton();
  expect(add).toBeDisabled();

  const describedBy = add.getAttribute('aria-describedby');
  expect(describedBy).toBeTruthy();
  const reason = document.getElementById(describedBy as string);
  expect(reason).toBeVisible();
  // The count, the cap, and both exits the ticket already has.
  expect(reason).toHaveTextContent(String(MAX_ACCEPTANCE_CRITERIA));
  expect(reason?.textContent?.toLowerCase()).toContain('remove');
  expect(reason?.textContent?.toLowerCase()).toContain('parent');
});

it('Add is enabled one criterion below the cap', () => {
  renderEditor(drafts(MAX_ACCEPTANCE_CRITERIA - 1));
  expect(addButton()).toBeEnabled();
  expect(addButton()).not.toHaveAttribute('aria-describedby');
});

it('an over-cap ticket can still be edited and shortened, and Add returns below the cap', () => {
  // The 78 live over-cap tickets: every row editable, every row removable.
  const { onChange, rerender } = renderEditor(drafts(20));
  expect(addButton()).toBeDisabled();

  fireEvent.change(screen.getByLabelText('Criterion 1'), { target: { value: 'reworded' } });
  expect(onChange).toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Remove criterion 20' }));
  expect(onChange).toHaveBeenCalled();

  // Shortened below the cap in the same draft — the stored count is still 20, so
  // the save would shrink the list and must not be pre-empted.
  rerender(drafts(MAX_ACCEPTANCE_CRITERIA - 1), 20);
  expect(addButton()).toBeEnabled();
});

it('Add stays disabled at the cap even when the stored list is shorter', () => {
  // A draft grown from 5 to 12 would be refused at 13: that is the cap term.
  renderEditor(drafts(MAX_ACCEPTANCE_CRITERIA), 5);
  expect(addButton()).toBeDisabled();
});

it('a multi-line paste that crosses the cap inserts every line', () => {
  // The pasted text is the operator's only copy. Dropping lines to respect the
  // cap would lose it silently; the readout goes over-cap instead.
  const rows = drafts(MAX_ACCEPTANCE_CRITERIA);
  const { onChange } = renderEditor(rows);
  fireEvent.change(screen.getByLabelText('Criterion 1'), {
    target: { value: 'first\nsecond\nthird' },
  });

  const next = onChange.mock.calls[0][0] as CriterionDraft[];
  expect(next).toHaveLength(MAX_ACCEPTANCE_CRITERIA + 2);
  expect(next.map((row) => row.text)).toEqual(
    expect.arrayContaining(['first', 'second', 'third']),
  );
});

/** Rows as the operator leaves them: some typed, some still blank. */
function mixedDrafts(filled: number, blanks: string[]): CriterionDraft[] {
  return criteriaDrafts([...Array.from({ length: filled }, (_, i) => `criterion ${i}`), ...blanks], []);
}

it('a blank row is not a criterion, so it counts against neither the readout nor Add', () => {
  // `draftCriteria` trims and drops blanks — it mirrors the server's
  // `normalize_criteria`, and that normalized list is what the save sends. So
  // 13 rows holding 11 criteria save as 11 and are accepted. A readout built on
  // `rows.length` says "13 of 12" and disables Add over a save the server would
  // take, which is the pre-emption the cap criterion rules out: the signal has
  // to predict the refusal, not approximate it.
  const rows = mixedDrafts(11, ['   ', '\n\t ']);
  expect(rows).toHaveLength(13);
  renderEditor(rows);

  expect(screen.getByText(new RegExp(`11 of ${MAX_ACCEPTANCE_CRITERIA}`))).toBeInTheDocument();
  expect(screen.queryByText(/over the cap/i)).not.toBeInTheDocument();
  expect(addButton()).toBeEnabled();
});

it('Add is disabled on twelve criteria even with blank rows past them', () => {
  // The other direction, so the test above cannot be satisfied by ignoring the
  // cap: 12 real criteria plus a blank row is still at the cap.
  renderEditor(mixedDrafts(MAX_ACCEPTANCE_CRITERIA, ['  ']));
  expect(
    screen.getByText(new RegExp(`${MAX_ACCEPTANCE_CRITERIA} of ${MAX_ACCEPTANCE_CRITERIA}`)),
  ).toBeInTheDocument();
  expect(addButton()).toBeDisabled();
});

it('a paste keeps every line whatever the line endings, and drops only the blanks', () => {
  // A paste off a Windows clipboard arrives as `\r\n`, and a paste out of a
  // document carries blank lines between the items. Splitting on `\n` alone
  // leaves a `\r` on the end of every criterion, which then reads as a *new*
  // criterion against the stored list and turns a re-save into growth. Pasted
  // text is the operator's only copy, so nothing non-blank may be dropped.
  const { onChange } = renderEditor(drafts(1));
  fireEvent.change(screen.getByLabelText('Criterion 1'), {
    target: { value: 'first\r\n\r\nsecond\r\n   \r\nthird' },
  });

  const next = onChange.mock.calls[0][0] as CriterionDraft[];
  // Order preserved, nothing non-blank lost, and no stray carriage return —
  // whether a blank line leaves an empty row behind is the implementer's call,
  // since an empty row is not a criterion either way.
  expect(next.map((row) => row.text).filter((text) => text.trim().length > 0)).toEqual([
    'first',
    'second',
    'third',
  ]);
  expect(next.some((row) => row.text.includes('\r'))).toBe(false);
});

it('a 62-criterion editor renders every row and every remove control', () => {
  // 62 is the largest list in the database, measured 2026-11-05.
  renderEditor(drafts(62));
  expect(screen.getAllByRole('textbox')).toHaveLength(62);
  for (const index of [1, 31, 62]) {
    expect(screen.getByLabelText(`Criterion ${index}`)).toHaveValue(`criterion ${index - 1}`);
    expect(screen.getByRole('button', { name: `Remove criterion ${index}` })).toBeEnabled();
  }
  expect(screen.getByText(new RegExp(`62 of ${MAX_ACCEPTANCE_CRITERIA}`))).toBeInTheDocument();
});

it('a 62-row editor adds no usability finding to the one it already has', () => {
  // 62 rows is the largest list in the database, and that length is where the
  // walls show up: `repeated-text`, `dead-end-list`, `tabular-list`. The editor
  // has to stay above all of them, which it does because every row is its own
  // labelled field with its own remove control.
  //
  // `unfiltered-long-list` is the exception, and it is accepted rather than
  // waived away: it already fires on a 62-row editor today, and the fix the
  // checker suggests — a filter beside the list — is a new affordance this
  // ticket's criteria explicitly rule out. Nobody searches a draft they are
  // typing. Pinned as the one allowed finding so a *new* one still reds.
  const { container } = renderEditor(drafts(62));
  const kinds = findUsabilityProblems(container).map((problem) => problem.kind);
  expect(kinds).toEqual(['unfiltered-long-list']);
});
