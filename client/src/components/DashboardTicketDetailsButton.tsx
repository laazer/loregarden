import React, { useState } from 'react';
import { TicketDetailsModal } from './TicketDetailsModal';
import { useTicketDetailsEditor } from '../hooks/useTicketDetailsEditor';

export interface DashboardTicketDetailsButtonProps {
  ticketId: string;
  className?: string;
}

export const DashboardTicketDetailsButton: React.FC<DashboardTicketDetailsButtonProps> = ({
  ticketId,
  className = '',
}) => {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const details = useTicketDetailsEditor(ticketId, isModalOpen);
  const isLoading = details.isLoading;

  const handleOpenModal = () => {
    details.clearSaveError();
    setIsModalOpen(true);
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
    details.clearSaveError();
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
        ticket={details.ticket}
        isOpen={isModalOpen}
        onClose={handleCloseModal}
        isLoading={isLoading}
        error={details.loadError}
        isSaving={details.isSaving}
        saveError={details.saveError}
        onSave={details.save}
        onSaveChecks={details.saveChecks}
        isSavingChecks={details.isSavingChecks}
      />
    </>
  );
};
