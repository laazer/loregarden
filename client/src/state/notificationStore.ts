import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { ToastTone } from "./toastStore";

export type InboxNotificationTone = ToastTone;

export interface InboxNotification {
  id: string;
  tone: InboxNotificationTone;
  title: string;
  message: string;
  createdAt: string;
  ticketId?: string;
  runId?: string;
}

export interface InboxNotificationInput {
  tone?: InboxNotificationTone;
  title: string;
  message?: string;
  ticketId?: string;
  runId?: string;
  createdAt?: string;
}

/** Cap so a busy queue overnight cannot fill localStorage. */
const MAX_NOTIFICATIONS = 50;

let seq = 0;

interface NotificationState {
  notifications: InboxNotification[];
  push: (input: InboxNotificationInput) => string;
  dismiss: (id: string) => void;
  clear: () => void;
}

export const useNotificationStore = create<NotificationState>()(
  persist(
    (set) => ({
      notifications: [],
      push: (input) => {
        const notification: InboxNotification = {
          id: `notif-${Date.now()}-${++seq}`,
          tone: input.tone ?? "info",
          title: input.title,
          message: input.message ?? "",
          createdAt: input.createdAt ?? new Date().toISOString(),
          ticketId: input.ticketId,
          runId: input.runId,
        };
        set((state) => ({
          notifications: [notification, ...state.notifications].slice(0, MAX_NOTIFICATIONS),
        }));
        return notification.id;
      },
      dismiss: (id) =>
        set((state) => ({
          notifications: state.notifications.filter((n) => n.id !== id),
        })),
      clear: () => set({ notifications: [] }),
    }),
    { name: "loregarden.inbox-notifications" },
  ),
);

/** Push from outside React (queue event bridge). */
export function pushInboxNotification(input: InboxNotificationInput): string {
  return useNotificationStore.getState().push(input);
}
