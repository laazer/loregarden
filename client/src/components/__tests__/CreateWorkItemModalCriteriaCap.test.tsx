/**
 * The create modal's criteria field against the cap (lg-milestone-that-2250).
 *
 * Create is always 0 -> N, so there is no stored count here: the readout is the
 * pre-flight signal and the 400 is the refusal. The field is free text — one
 * criterion per line — so the count is the non-blank lines, the same rule
 * `parseAcceptanceCriteriaText` applies on submit.
 */
import { fireEvent, render, screen } from '@testing-library/react';

import { CreateWorkItemModal } from '../CreateWorkItemModal';
import { MAX_ACCEPTANCE_CRITERIA } from '../../lib/criterionDrafts';

function renderModal(props: Partial<React.ComponentProps<typeof CreateWorkItemModal>> = {}) {
  return render(
    <CreateWorkItemModal
      open
      workspaceSlug="loregarden"
      workspacePicker={false}
      workspaces={[{ slug: 'loregarden', name: 'Loregarden' }]}
      onWorkspaceSlugChange={() => {}}
      tickets={[]}
      selectedTicketId={null}
      ticketTree={[]}
      isSaving={false}
      onClose={() => {}}
      onCreate={jest.fn().mockResolvedValue(undefined)}
      {...props}
    />,
  );
}

const criteriaField = () => screen.getByLabelText(/acceptance criteria/i);

it('the criteria field counts lines against the cap', () => {
  renderModal();
  expect(screen.getByText(new RegExp(`0 of ${MAX_ACCEPTANCE_CRITERIA}`))).toBeInTheDocument();

  // Blank lines and bullet markers are not criteria, so they are not counted.
  fireEvent.change(criteriaField(), { target: { value: '- one\n\n- two\n   \n- three' } });
  expect(screen.getByText(new RegExp(`3 of ${MAX_ACCEPTANCE_CRITERIA}`))).toBeInTheDocument();

  const over = Array.from({ length: MAX_ACCEPTANCE_CRITERIA + 1 }, (_, i) => `c${i}`).join('\n');
  fireEvent.change(criteriaField(), { target: { value: over } });
  expect(
    screen.getByText(new RegExp(`${MAX_ACCEPTANCE_CRITERIA + 1} of ${MAX_ACCEPTANCE_CRITERIA}`)),
  ).toBeInTheDocument();
  expect(screen.getByText(/over the cap/i)).toBeInTheDocument();
});

it('a refusal renders in the existing error slot with the draft kept', () => {
  const over = Array.from({ length: MAX_ACCEPTANCE_CRITERIA + 1 }, (_, i) => `c${i}`).join('\n');
  const message =
    `This ticket would hold ${MAX_ACCEPTANCE_CRITERIA + 1} acceptance criteria; the cap is ` +
    `${MAX_ACCEPTANCE_CRITERIA}. Remove a criterion, or open a new ticket under this one's parent.`;
  renderModal({ errorMessage: message });

  fireEvent.change(criteriaField(), { target: { value: over } });

  expect(screen.getByText(message)).toBeInTheDocument();
  // The draft is the operator's only copy of what they typed.
  expect(criteriaField()).toHaveValue(over);
});

it('a refused create keeps the draft across the re-render it causes', () => {
  // The refusal arrives as a re-render: the mutation's error lands in
  // `errorMessage`, and the lists the modal seeds its defaults from come back
  // with fresh array identities. Before the fix that re-ran `initialDraft` and
  // blanked every field under a message saying the draft was still there.
  const over = Array.from({ length: MAX_ACCEPTANCE_CRITERIA + 1 }, (_, i) => `c${i}`).join('\n');
  const props = {
    open: true as const,
    workspaceSlug: 'loregarden',
    workspacePicker: false,
    workspaces: [{ slug: 'loregarden', name: 'Loregarden' }],
    onWorkspaceSlugChange: () => {},
    selectedTicketId: null,
    isSaving: false,
    onClose: () => {},
    onCreate: jest.fn(),
  };
  const { rerender } = render(
    <CreateWorkItemModal {...props} tickets={[]} ticketTree={[]} errorMessage={null} />,
  );

  fireEvent.change(screen.getByLabelText(/^title$/i), { target: { value: 'draft-retention probe' } });
  fireEvent.change(screen.getByLabelText(/^description$/i), { target: { value: 'typed prose' } });
  fireEvent.change(criteriaField(), { target: { value: over } });
  fireEvent.change(screen.getByLabelText(/^priority$/i), { target: { value: '1' } });

  fireEvent.click(screen.getByRole('button', { name: /create work item/i }));
  expect(props.onCreate).toHaveBeenCalledTimes(1);

  // Same data, new identities — exactly what the failed mutation's re-render gives.
  rerender(
    <CreateWorkItemModal
      {...props}
      tickets={[]}
      ticketTree={[]}
      errorMessage="This ticket would hold 13 acceptance criteria; the cap is 12."
    />,
  );

  expect(screen.getByLabelText(/^title$/i)).toHaveValue('draft-retention probe');
  expect(screen.getByLabelText(/^description$/i)).toHaveValue('typed prose');
  expect(criteriaField()).toHaveValue(over);
  expect(screen.getByLabelText(/^priority$/i)).toHaveValue('1');
  expect(
    screen.getByText(new RegExp(`${MAX_ACCEPTANCE_CRITERIA + 1} of ${MAX_ACCEPTANCE_CRITERIA}`)),
  ).toBeInTheDocument();
});
