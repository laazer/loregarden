import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import * as apiClient from '../../api/client';
import { TicketDetailsModal, type TicketDetailsModalProps } from '../TicketDetailsModal';

jest.mock('../../api/client', () => jest.requireActual('../../test/apiClientMock'));

function mockTicket(over: Partial<apiClient.TicketDetail> = {}): apiClient.TicketDetail {
  return {
    id: 'ticket-uuid-1',
    external_id: 'lg-modal-12',
    title: 'Split the modal',
    description: 'Uses **bold** and `code`.',
    acceptance_criteria: ['View is read-only', 'Edit can change text'],
    checked_acceptance_criteria: ['View is read-only'],
    state: 'in_progress',
    priority: 2,
    workspace_slug: 'loregarden',
    workflow_stage_key: '',
    workflow_stage_status: 'pending',
    workflow_stage_name: '',
    run_code: '',
    work_item_type: 'task',
    parent_ticket_id: null,
    milestone: '',
    branch: '',
    child_count: 0,
    revision: 1,
    last_updated_by: '',
    current_stage_agent: '',
    next_status: '',
    blocking_issues: '',
    state_locked: false,
    workflow_template_slug: '',
    workflow_template_name: '',
    stages: [],
    artifacts: { diff: null, logs: [], tests: null, context: [], error: null, live: null },
    ...over,
  };
}

function renderModal(props: Partial<TicketDetailsModalProps> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onClose = jest.fn();
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <TicketDetailsModal ticket={mockTicket()} isOpen onClose={onClose} {...props} />
    </QueryClientProvider>,
  );
  return { ...utils, onClose };
}

const editButton = () => screen.getByRole('button', { name: /^edit$/i });

describe('read mode', () => {
  it('renders the description as markdown rather than in a text field', () => {
    renderModal({ onSave: jest.fn() });

    expect(screen.getByText('bold').tagName).toBe('STRONG');
    expect(screen.getByText('code').tagName).toBe('CODE');
    expect(screen.queryByLabelText(/description/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Ticket title')).not.toBeInTheDocument();
  });

  it('shows criteria as a checklist reflecting what is checked, with no editable text', () => {
    renderModal({ onSave: jest.fn(), onSaveChecks: jest.fn() });

    const list = screen.getByRole('list', { name: /acceptance criteria/i });
    expect(within(list).getByRole('checkbox', { name: 'View is read-only' })).toBeChecked();
    expect(within(list).getByRole('checkbox', { name: 'Edit can change text' })).not.toBeChecked();
    expect(within(list).queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.getByText('1 of 2 done')).toBeInTheDocument();
  });

  it('saves a tick through onSaveChecks, in criteria order', async () => {
    const onSaveChecks = jest.fn().mockResolvedValue(undefined);
    renderModal({ onSaveChecks });

    fireEvent.click(screen.getByRole('checkbox', { name: 'Edit can change text' }));

    await waitFor(() => {
      expect(onSaveChecks).toHaveBeenCalledWith(['View is read-only', 'Edit can change text']);
    });
    expect(screen.getByRole('checkbox', { name: 'Edit can change text' })).toBeChecked();
  });

  it('can uncheck a criterion', async () => {
    const onSaveChecks = jest.fn().mockResolvedValue(undefined);
    renderModal({ onSaveChecks });

    fireEvent.click(screen.getByRole('checkbox', { name: 'View is read-only' }));

    await waitFor(() => expect(onSaveChecks).toHaveBeenCalledWith([]));
  });

  it('puts the box back when the save rejects', async () => {
    const onSaveChecks = jest.fn().mockRejectedValue(new Error('nope'));
    renderModal({ onSaveChecks });

    fireEvent.click(screen.getByRole('checkbox', { name: 'Edit can change text' }));

    await waitFor(() => expect(onSaveChecks).toHaveBeenCalled());
    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: 'Edit can change text' })).not.toBeChecked();
    });
  });

  it('disables the boxes while a check is saving, so a second click cannot race it', () => {
    renderModal({ onSaveChecks: jest.fn(), isSavingChecks: true });

    for (const box of screen.getAllByRole('checkbox')) expect(box).toBeDisabled();
  });

  it('shows read-only boxes and no Edit button when nothing can save', () => {
    renderModal();

    for (const box of screen.getAllByRole('checkbox')) expect(box).toBeDisabled();
    expect(screen.queryByRole('button', { name: /^edit$/i })).not.toBeInTheDocument();
  });

  it('says what is missing when there is no description or criteria', () => {
    renderModal({ ticket: mockTicket({ description: '', acceptance_criteria: [], checked_acceptance_criteria: [] }), onSave: jest.fn() });

    expect(screen.getByText(/no description/i)).toBeInTheDocument();
    expect(screen.getByText(/no acceptance criteria yet/i)).toBeInTheDocument();
  });
});

describe('copying', () => {
  const writeText = jest.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    writeText.mockClear();
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  });

  it('copies the ticket number', async () => {
    renderModal();

    fireEvent.click(screen.getByRole('button', { name: 'Copy ticket number' }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith('lg-modal-12'));
    expect(await screen.findByRole('button', { name: 'Copied ticket number' })).toBeInTheDocument();
  });

  it('copies the ticket name', async () => {
    renderModal();

    fireEvent.click(screen.getByRole('button', { name: 'Copy ticket name' }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith('Split the modal'));
  });

  it('falls back to the internal id when the ticket has no external id', async () => {
    renderModal({ ticket: mockTicket({ external_id: '' }) });

    fireEvent.click(screen.getByRole('button', { name: 'Copy ticket number' }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith('ticket-uuid-1'));
  });
});

describe('edit mode', () => {
  it('lets criteria be both reworded and checked, and saves both', async () => {
    const onSave = jest.fn().mockResolvedValue(undefined);
    renderModal({ onSave });
    fireEvent.click(editButton());

    fireEvent.change(screen.getByLabelText('Criterion 2'), { target: { value: 'Edit can reword' } });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Criterion 2 done' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Criterion 1 done' }));
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({
          acceptanceCriteria: ['View is read-only', 'Edit can reword'],
          checkedAcceptanceCriteria: ['Edit can reword'],
        }),
      );
    });
  });

  it('removes a criterion and drops its check with it', async () => {
    const onSave = jest.fn().mockResolvedValue(undefined);
    renderModal({ onSave });
    fireEvent.click(editButton());

    fireEvent.click(screen.getByRole('button', { name: 'Remove criterion 1' }));
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({ acceptanceCriteria: ['Edit can change text'], checkedAcceptanceCriteria: [] }),
      );
    });
  });

  it('adds a criterion below on Enter', () => {
    renderModal({ onSave: jest.fn() });
    fireEvent.click(editButton());

    fireEvent.keyDown(screen.getByLabelText('Criterion 1'), { key: 'Enter' });

    expect(screen.getByLabelText('Criterion 2')).toHaveValue('');
    expect(screen.getByLabelText('Criterion 3')).toHaveValue('Edit can change text');
  });

  it('splits pasted lines into one criterion each', () => {
    renderModal({ onSave: jest.fn() });
    fireEvent.click(editButton());

    fireEvent.change(screen.getByLabelText('Criterion 1'), { target: { value: 'First\nSecond\n\nThird' } });

    expect(screen.getByLabelText('Criterion 1')).toHaveValue('First');
    expect(screen.getByLabelText('Criterion 2')).toHaveValue('Second');
    expect(screen.getByLabelText('Criterion 3')).toHaveValue('Third');
    expect(screen.getByLabelText('Criterion 4')).toHaveValue('Edit can change text');
  });

  it('Cancel discards the draft and returns to read mode', () => {
    const onSave = jest.fn();
    renderModal({ onSave });
    fireEvent.click(editButton());

    fireEvent.change(screen.getByLabelText('Ticket title'), { target: { value: 'Thrown away' } });
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByRole('heading', { name: 'Split the modal' })).toBeInTheDocument();
  });

  it('Escape leaves edit mode before it closes the modal', () => {
    const { onClose } = renderModal({ onSave: jest.fn() });
    fireEvent.click(editButton());

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('Ticket title')).not.toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('stays in edit mode with the draft intact when the save rejects', async () => {
    const onSave = jest.fn().mockRejectedValue(new Error('conflict'));
    renderModal({ onSave });
    fireEvent.click(editButton());

    fireEvent.change(screen.getByLabelText('Ticket title'), { target: { value: 'Kept' } });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(screen.getByLabelText('Ticket title')).toHaveValue('Kept');
  });
});
