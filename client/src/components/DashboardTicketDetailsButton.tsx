import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { TicketDetailsModal } from './TicketDetailsModal';
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

  const { data: ticketDetail, isLoading, error } = useQuery({
    queryKey: ['ticket', ticketId],
    queryFn: () => apiClient.api.ticket(ticketId),
    enabled: isModalOpen,
  });

  const handleOpenModal = () => {
    setIsModalOpen(true);
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
  };

  return (
    <>
      <button
        onClick={handleOpenModal}
        disabled={isLoading}
        className={`inline-flex items-center gap-2 px-3 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-400 text-white rounded-md transition-colors ${className}`}
        aria-label="View ticket details"
      >
        {isLoading ? (
          <>
            <span className="inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
            <span>Loading...</span>
          </>
        ) : (
          <>
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span>View Details</span>
          </>
        )}
      </button>

      {ticketDetail && (
        <TicketDetailsModal
          ticket={ticketDetail}
          isOpen={isModalOpen}
          onClose={handleCloseModal}
          isLoading={isLoading}
          error={error ? (error instanceof Error ? error.message : 'Failed to load ticket details') : undefined}
        />
      )}
    </>
  );
};
