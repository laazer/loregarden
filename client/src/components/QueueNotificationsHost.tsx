import { useEffect } from "react";

import { announceQueueEvent } from "../lib/queueNotifications";
import { useQueueStatus } from "../state/QueueStatusContext";

/**
 * Toast + inbox notifications for queue events, mounted once for the app.
 *
 * Lives outside the queue page so a run finishing while the operator is in
 * chat or the console still lands in the inbox drawer.
 */
export function QueueNotificationsHost() {
  const { onQueueEvent } = useQueueStatus();

  useEffect(() => onQueueEvent((event) => announceQueueEvent(event)), [onQueueEvent]);

  return null;
}
