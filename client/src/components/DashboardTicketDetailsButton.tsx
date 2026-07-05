import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { TicketDetailsModal, type TicketDetailsSaveDraft } from './TicketDetailsModal';
import * as apiClient from '../api/client';

export interface DashboardTicketDetailsButtonProps {
  ticketId: string;
  ticket?: apiClient.TicketSummary;
  className?: string;
}

export const DashboardTicketDetailsButton: React.FC<DashboardTicketDetailsButtonProps> = ({
  ticketId,
  ticket,
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
    await saveDetails.mutateAsync(draft);
  };

  return (
    <>
      <button
        onClick={handleOpenModal}
        disabled={isLoading}
        className={`btn-secondary btn-compact ${className}`}
        aria-label="View ticket details"
        type="button"
      >
        {isLoading ? (
          <>
            <span style={{ display: "inline-block", width: 4, height: 4, borderRadius: "50%", border: "2px solid currentColor", borderTopColor: "transparent", animation: "spin 0.8s linear infinite" }} />
            <span>Loading...</span>
          </>
        ) : (
          <>
            <svg style={{ width: 14, height: 14, marginRight: 4 }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
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
