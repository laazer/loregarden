import { useEffect, useLayoutEffect, useRef } from "react";

import type { UiActionHandler, UiActionName } from "./catalog";
import { uiActionRegistry } from "./registry";

/**
 * Offer `name` to agents while this component is mounted.
 *
 * Put it beside the control it drives, so the two cannot drift apart: when the
 * control unmounts, the action is withdrawn. The handler may change every
 * render; only mounting and unmounting re-register. Throw from the handler to
 * refuse — the message reaches the agent as the reason.
 */
export function useAgentAction<N extends UiActionName>(
  name: N,
  handler: UiActionHandler<N>,
  enabled = true,
): void {
  const latest = useRef(handler);
  // Not assigned during render: React may render without committing.
  useLayoutEffect(() => {
    latest.current = handler;
  });

  useEffect(() => {
    if (!enabled) return undefined;
    return uiActionRegistry.register(name, (args) => latest.current(args));
  }, [name, enabled]);
}
