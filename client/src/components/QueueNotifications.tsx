/**
 * Queue event notifications component
 * Displays toast-style alerts for queue events (run complete, promoted, error)
 */

import { forwardRef, useCallback, useImperativeHandle, useState } from 'react';
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

/** Push a notification onto the queue toast stack. */
export interface QueueNotificationsHandle {
  notify: (event: NotificationEvent) => void;
}

// workspaceId stays in the props: callers already pass it, and it is what a
// subscription would key on. Not destructured while nothing subscribes.
export const QueueNotifications = forwardRef<
  QueueNotificationsHandle,
  QueueNotificationsProps
>(function QueueNotifications(_props, ref) {
  const [notifications, setNotifications] = useState<DisplayNotification[]>([]);

  // The entry point delivery would call. Exposed on the ref so the toast stack
  // stays reachable rather than being unreachable code waiting on a producer.
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

  // Queue notifications are not wired up.
  //
  // This subscribed over SSE to /api/parallel/workspace/:id/notifications, and
  // first probed a ws-status endpoint. Neither has ever existed: the SSE route
  // is written against a ws.subscribe API that websocket_events does not
  // provide, so it cannot even be imported, let alone registered. No event has
  // ever reached this component — it just 404'd on every queue load.
  //
  // The rendering below is kept so notifications work the moment something can
  // push them; addNotification is the entry point. Delivery needs subscribe /
  // unsubscribe on the websocket layer first.

  useImperativeHandle(ref, () => ({ notify: addNotification }), [addNotification]);

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
});
