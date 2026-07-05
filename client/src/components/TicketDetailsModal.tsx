import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import * as apiClient from '../api/client';

export interface TicketDetailsModalProps {
  ticket: apiClient.TicketDetail | null;
  isOpen: boolean;
  onClose: () => void;
  isLoading?: boolean;
  error?: string;
}

export const TicketDetailsModal: React.FC<TicketDetailsModalProps> = ({
  ticket,
  isOpen,
  onClose,
  isLoading = false,
  error,
}) => {
  const [selectedArtifactTab, setSelectedArtifactTab] = useState<string>('overview');

  if (!ticket) {
    return null;
  }

  if (!isOpen) {
    return null;
  }

  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) {
      onClose();
    }
  };

  const handleEscapeKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      onClose();
    }
  };

  return (
    <div
      role="presentation"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50"
      onClick={handleBackdropClick}
      data-testid="modal-backdrop"
    >
      <div
        role="dialog"
        className="relative w-full max-w-4xl max-h-[90vh] bg-white rounded-lg shadow-xl flex flex-col"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleEscapeKey}
        tabIndex={-1}
        data-testid="modal-content"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <div className="flex-1">
            <h2 className="text-2xl font-bold text-gray-900">{ticket.title}</h2>
            <p className="text-sm text-gray-500 mt-1">{ticket.external_id}</p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors"
            aria-label="close"
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {error && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-red-700">
              {error}
            </div>
          )}

          {isLoading && (
            <div className="flex items-center justify-center py-8">
              <div className="text-gray-600">Loading ticket details...</div>
            </div>
          )}

          {!isLoading && !error && (
            <>
              {/* Status Section */}
              <div className="mb-6">
                <h3 className="text-lg font-semibold text-gray-900 mb-2">Status</h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <span className="text-sm text-gray-600">State</span>
                    <div className="mt-1 inline-block px-3 py-1 rounded-full bg-blue-100 text-blue-800 text-sm font-medium">
                      {ticket.state}
                    </div>
                  </div>
                  <div>
                    <span className="text-sm text-gray-600">Priority</span>
                    <div className="mt-1 text-gray-900 font-medium">{ticket.priority}</div>
                  </div>
                  <div>
                    <span className="text-sm text-gray-600">Type</span>
                    <div className="mt-1 text-gray-900 font-medium capitalize">{ticket.work_item_type}</div>
                  </div>
                  {ticket.workflow_stage_name && (
                    <div>
                      <span className="text-sm text-gray-600">Workflow Stage</span>
                      <div className="mt-1 text-gray-900 font-medium">{ticket.workflow_stage_name}</div>
                    </div>
                  )}
                </div>
              </div>

              {/* Description Section */}
              {ticket.description && (
                <div className="mb-6">
                  <h3 className="text-lg font-semibold text-gray-900 mb-2">Description</h3>
                  <p className="text-gray-700 whitespace-pre-wrap">{ticket.description}</p>
                </div>
              )}

              {/* Acceptance Criteria Section */}
              {ticket.acceptance_criteria && ticket.acceptance_criteria.length > 0 && (
                <div className="mb-6">
                  <h3 className="text-lg font-semibold text-gray-900 mb-2">Acceptance Criteria</h3>
                  <ul className="space-y-2">
                    {ticket.acceptance_criteria.map((criterion, index) => (
                      <li key={index} className="flex items-start">
                        <span className="text-green-600 mr-2 mt-1">✓</span>
                        <span className="text-gray-700">{criterion}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Blocking Issues */}
              {ticket.blocking_issues && (
                <div className="mb-6 p-4 bg-yellow-50 border border-yellow-200 rounded">
                  <h3 className="text-lg font-semibold text-yellow-900 mb-2">Blocking Issues</h3>
                  <p className="text-yellow-800">{ticket.blocking_issues}</p>
                </div>
              )}

              {/* Stages Section */}
              {ticket.stages && ticket.stages.length > 0 && (
                <div className="mb-6">
                  <h3 className="text-lg font-semibold text-gray-900 mb-3">Workflow Stages</h3>
                  <div className="space-y-2">
                    {ticket.stages.map((stage) => (
                      <div key={stage.key} className="flex items-center justify-between p-3 bg-gray-50 rounded">
                        <div>
                          <div className="font-medium text-gray-900">{stage.name}</div>
                          <div className="text-xs text-gray-600">Agent: {stage.agent_id || 'N/A'}</div>
                        </div>
                        <div className="inline-block px-3 py-1 rounded text-sm font-medium bg-gray-200 text-gray-800">
                          {stage.status}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Artifacts Section */}
              {ticket.artifacts && Object.keys(ticket.artifacts).some((k) => ticket.artifacts[k]) && (
                <div className="mb-6">
                  <h3 className="text-lg font-semibold text-gray-900 mb-3">Artifacts</h3>
                  <div className="space-y-3">
                    {ticket.artifacts.diff && (
                      <div className="p-3 bg-gray-50 rounded">
                        <div className="font-medium text-gray-900">Code Diff</div>
                        <div className="text-xs text-gray-600 mt-1">
                          Files: {ticket.artifacts.diff.files || '?'} | Added: {ticket.artifacts.diff.add || '0'} | Removed:{' '}
                          {ticket.artifacts.diff.del || '0'}
                        </div>
                      </div>
                    )}
                    {ticket.artifacts.tests && (
                      <div className="p-3 bg-gray-50 rounded">
                        <div className="font-medium text-gray-900">Test Results</div>
                        <div className="text-xs text-gray-600 mt-1">{ticket.artifacts.tests.summary}</div>
                      </div>
                    )}
                    {ticket.artifacts.logs && ticket.artifacts.logs.length > 0 && (
                      <div className="p-3 bg-gray-50 rounded">
                        <div className="font-medium text-gray-900">Logs</div>
                        <div className="text-xs text-gray-600 mt-1">{ticket.artifacts.logs.length} log entries</div>
                      </div>
                    )}
                    {ticket.artifacts.error && (
                      <div className="p-3 bg-red-50 border border-red-200 rounded">
                        <div className="font-medium text-red-900">Error</div>
                        <div className="text-xs text-red-700 mt-1">{ticket.artifacts.error.message}</div>
                      </div>
                    )}
                    {ticket.artifacts.live && (
                      <div className="p-3 bg-blue-50 rounded">
                        <div className="font-medium text-blue-900">Status</div>
                        <div className="text-xs text-blue-700 mt-1">{ticket.artifacts.live}</div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Metadata Section */}
              <div className="mb-6 pt-4 border-t">
                <h3 className="text-lg font-semibold text-gray-900 mb-2">Metadata</h3>
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <span className="text-gray-600">ID</span>
                    <div className="mt-1 font-mono text-xs text-gray-800 break-all">{ticket.id}</div>
                  </div>
                  <div>
                    <span className="text-gray-600">Last Updated By</span>
                    <div className="mt-1 text-gray-900">{ticket.last_updated_by || 'N/A'}</div>
                  </div>
                  <div>
                    <span className="text-gray-600">Revision</span>
                    <div className="mt-1 text-gray-900">{ticket.revision}</div>
                  </div>
                  <div>
                    <span className="text-gray-600">Milestone</span>
                    <div className="mt-1 text-gray-900">{ticket.milestone || 'N/A'}</div>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
