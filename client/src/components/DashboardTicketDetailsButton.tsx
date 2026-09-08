import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { TicketDetailsModal, type TicketDetailsSaveDraft } from './TicketDetailsModal';
import { describeError } from '../state/toastStore';
import * as apiClient from '../api/client';

export interface DashboardTicketDetailsButtonProps {
  ticketId: string;
  className?: string;
}

export const DashboardTicketDetailsButton: React.FC<DashboardTicketDetailsButtonProps> = ({
  ticketId,
  className = '',
}) => {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [saveError, setSaveError] = useState<string | undefined>();
  const qc = useQueryClient();

  const { data: ticketDetail, isLoading, error } = useQuery({
    queryKey: ['ticket', ticketId],
    queryFn: () => apiClient.api.ticket(ticketId),
    enabled: isModalOpen,
  });

  const saveDetails = useMutation({
    meta: { errorTitle: "Save ticket details" },
    mutationFn: async (draft: TicketDetailsSaveDraft) => {
      const patch: Parameters<typeof apiClient.api.updateTicket>[1] = {};
      const current = ticketDetail;
      if (!current) return;

      if (draft.title !== current.title) {
        patch.title = draft.title;
      }
      if (draft.description !== (current.description ?? '')) {
        patch.description = draft.description;
      }
      const currentCriteria = current.acceptance_criteria ?? [];
      if (draft.acceptanceCriteria.join('\n') !== currentCriteria.join('\n')) {
        patch.acceptance_criteria = draft.acceptanceCriteria;
      }
      const currentTags = current.tags ?? [];
      if (draft.tags.join(',') !== currentTags.join(',')) {
        patch.tags = draft.tags;
      }
      if (draft.state !== current.state) {
        patch.state = draft.state;
      }
      if (draft.priority !== current.priority) {
        patch.priority = draft.priority;
      }
      if (Object.keys(patch).length === 0) return;

      await apiClient.api.updateTicket(ticketId, patch);
    },
    onSuccess: () => {
      setSaveError(undefined);
      qc.invalidateQueries({ queryKey: ['ticket', ticketId] });
      qc.invalidateQueries({ queryKey: ['ticket-tree'] });
      qc.invalidateQueries({ queryKey: ['tickets'] });
    },
    onError: (err) => {
      setSaveError(describeError(err, 'Failed to save ticket details'));
    },
  });

  const handleOpenModal = () => {
    setSaveError(undefined);
    setIsModalOpen(true);
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
    setSaveError(undefined);
  };

  const handleSave = async (draft: TicketDetailsSaveDraft) => {
    setSaveError(undefined);
    try {
      await saveDetails.mutateAsync(draft);
    } catch {
      // silent-ok: the mutation's onError puts the message in `saveError`,
      // which is handed to TicketDetailsModal and rendered there.
    }
  };

  return (
    <>
      <button
        onClick={handleOpenModal}
        className={`btn-secondary btn-compact dashboard-details-btn ${className}`}
        aria-label="View ticket details"
        type="button"
        disabled={isLoading && isModalOpen}
      >
        {isLoading && isModalOpen ? (
          <>
            <span className="dashboard-details-spinner" aria-hidden="true" />
            <span>Loading...</span>
          </>
        ) : (
          <>
            <svg className="dashboard-details-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>Details</span>
          </>
        )}
      </button>

      <TicketDetailsModal
        ticket={ticketDetail || null}
        isOpen={isModalOpen}
        onClose={handleCloseModal}
        isLoading={isLoading}
        error={error ? (error instanceof Error ? error.message : 'Failed to load ticket details') : undefined}
        isSaving={saveDetails.isPending}
        saveError={saveError}
        onSave={handleSave}
      />
    </>
  );
};
