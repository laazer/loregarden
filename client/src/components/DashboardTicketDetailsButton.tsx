import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { TicketDetailsModal, type TicketDetailsSaveDraft } from './TicketDetailsModal';
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
      setSaveError(err instanceof Error ? err.message : 'Failed to save ticket details');
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
      // saveError is set via mutation onError
    }
  };

  return (
    <>
      <button
        onClick={handleOpenModal}
        className={`btn-secondary btn-compact dashboard-details-btn ${className}`}
        aria-label="View ticket details"
        type="button"
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
