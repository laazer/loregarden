import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { Dashboard } from '../Dashboard';
import { RouterBridgeSync } from '../../components/RouterBridgeSync';
import * as apiClient from '../../api/client';
import type { Approval } from '../../api/chatTypes';
import { useUiStore } from '../../state/uiStore';

jest.mock('../../api/client', () => jest.requireActual('../../test/apiClientMock'));

const TICKET_ID = '00000000-0000-4000-8000-000000000335';

/**
 * Every pending approval on a ticket waits on a person. Counting only the ones
 * carrying criteria hid a milestone's human_action (an integration merge to
 * resolve by hand) from both the timeline and the Approvals tab badge.
 */
describe('Dashboard - pending approvals on the selected ticket', () => {
  beforeEach(() => {
    useUiStore.setState({
      stateFilters: [],
      typeFilters: [],
      search: '',
      expandedTicketIds: [],
      workspace: 'all',
      paneVisibility: { workspaces: true, tickets: true, workflow: true, artifacts: true },
    });
    jest.clearAllMocks();
    jest.mocked(apiClient.api.workspaces).mockResolvedValue([]);
    jest.mocked(apiClient.api.ticketTree).mockResolvedValue([]);
    jest.mocked(apiClient.api.ticket).mockResolvedValue({
      id: TICKET_ID,
      external_id: 'lg-durable-remote-335',
      title: 'Durable & remote agent operations',
      state: 'in_progress',
      work_item_type: 'milestone',
      workflow_stage_key: 'triage',
      workflow_stage_status: 'pending',
      child_count: 0,
      blocking_issues: '',
      stages: [],
      artifacts: {},
    } as unknown as apiClient.TicketDetail);
    jest.mocked(apiClient.api.runs).mockResolvedValue([]);
    jest.mocked(apiClient.api.ticketLedger).mockResolvedValue({
      visits: [],
      total_runs: 0,
      reworked_stages: [],
      total_seconds: 0,
    });
    jest.mocked(apiClient.api.ticketArtifacts).mockResolvedValue({ items: [], total: 0 });
    jest.mocked(apiClient.api.approvals).mockResolvedValue([
      { id: 'a1', kind: 'human_action', status: 'pending', ticket_id: TICKET_ID } as unknown as Approval,
    ]);
  });

  it('counts a human_action approval in the timeline and on the tab', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={[`/tickets/${TICKET_ID}/timeline`]}>
          <RouterBridgeSync />
          <Routes>
            <Route path="/tickets/:ticketId/:artifactTab" element={<Dashboard />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('1 approval waiting on you')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Approvals/ })).toHaveTextContent('1');
  });
});
