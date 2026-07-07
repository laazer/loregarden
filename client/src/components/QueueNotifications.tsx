/**
 * Queue event notifications component
 * Displays toast-style alerts for queue events (run complete, promoted, error)
 */

import React, { useState, useEffect, useCallback } from 'react';
import { IconCloseButton } from './IconCloseButton';
import './QueueNotifications.css';

export interface NotificationEvent {
  id: string;
  type: 'success' | 'error' | 'info' | 'warning';
  title: string;
  message: string;
  duration?: number; // ms, 0 = persistent
  action?: {
    label: string;
    onClick: () => void;
  };
}

interface DisplayNotification extends NotificationEvent {
  timestamp: number;
  isExiting: boolean;
}

export interface QueueNotificationsProps {
  workspaceId: string;
}

export function QueueNotifications({ workspaceId }: QueueNotificationsProps) {
  const [notifications, setNotifications] = useState<DisplayNotification[]>([]);
  const [eventSource, setEventSource] = useState<EventSource | null>(null);

  // Add a new notification
  const addNotification = useCallback(
    (event: NotificationEvent) => {
      const id = event.id || `${Date.now()}-${Math.random()}`;
      const displayNotification: DisplayNotification = {
        ...event,
        id,
        timestamp: Date.now(),
        isExiting: false,
      };

      setNotifications((prev) => [...prev, displayNotification]);

      // Auto-dismiss if duration is set
      if (event.duration !== 0) {
        const timeout = setTimeout(() => {
          removeNotification(id);
        }, event.duration || 5000);

        return () => clearTimeout(timeout);
      }
    },
    []
  );

  // Remove a notification with animation
  const removeNotification = useCallback((id: string) => {
    setNotifications((prev) =>
      prev.map((notif) =>
        notif.id === id ? { ...notif, isExiting: true } : notif
      )
    );

    // Remove from DOM after animation completes
    setTimeout(() => {
      setNotifications((prev) => prev.filter((notif) => notif.id !== id));
    }, 300);
  }, []);

  // Subscribe to WebSocket events for queue updates
  useEffect(() => {
    // Check for WebSocket availability
    const checkWebSocket = async () => {
      try {
        // Try to get WebSocket client status
        const response = await fetch(
          `/api/parallel/workspace/${workspaceId}/ws-status`
        ).catch(() => null);

        if (!response?.ok) {
          // Fall back to Server-Sent Events for notifications
          setupSSE();
          return;
        }
      } catch {
        setupSSE();
      }
    };

    const setupSSE = () => {
      // Setup Server-Sent Events for notifications
      const sse = new EventSource(
        `/api/parallel/workspace/${workspaceId}/notifications`
      );

      sse.addEventListener('run_completed', (event) => {
        const data = JSON.parse(event.data);
        addNotification({
          id: `run-completed-${data.run_id}`,
          type: 'success',
          title: 'Run Completed',
          message: `${data.ticket_id} finished successfully`,
          duration: 5000,
        });
      });

      sse.addEventListener('run_promoted', (event) => {
        const data = JSON.parse(event.data);
        addNotification({
          id: `run-promoted-${data.run_id}`,
          type: 'info',
          title: 'Run Promoted',
          message: `${data.ticket_id} started (slot ${data.slot_number})`,
          duration: 5000,
        });
      });

      sse.addEventListener('run_failed', (event) => {
        const data = JSON.parse(event.data);
        addNotification({
          id: `run-failed-${data.run_id}`,
          type: 'error',
          title: 'Run Failed',
          message: `${data.ticket_id} failed: ${data.error}`,
          duration: 10000,
        });
      });

      sse.addEventListener('reorder_failed', (event) => {
        const data = JSON.parse(event.data);
        addNotification({
          id: `reorder-failed-${data.run_id}`,
          type: 'error',
          title: 'Reorder Failed',
          message: data.message,
          duration: 5000,
        });
      });

      setEventSource(sse);
    };

    checkWebSocket();

    return () => {
      if (eventSource) {
        eventSource.close();
      }
    };
  }, [workspaceId, addNotification]);

  return (
    <div className="queue-notifications-container">
      {notifications.map((notif) => (
        <div
          key={notif.id}
          className={`notification notification-${notif.type} ${
            notif.isExiting ? 'exiting' : ''
          }`}
          role="alert"
          aria-live="polite"
        >
          <div className="notification-icon">
            {notif.type === 'success' && '✓'}
            {notif.type === 'error' && '✕'}
            {notif.type === 'info' && 'ℹ'}
            {notif.type === 'warning' && '⚠'}
          </div>

          <div className="notification-content">
            <div className="notification-title">{notif.title}</div>
            <div className="notification-message">{notif.message}</div>
          </div>

          {notif.action && (
            <button
              className="notification-action"
              onClick={notif.action.onClick}
            >
              {notif.action.label}
            </button>
          )}

          <IconCloseButton
            onClick={() => removeNotification(notif.id)}
            aria-label="Dismiss notification"
          />
        </div>
      ))}
    </div>
  );
}
