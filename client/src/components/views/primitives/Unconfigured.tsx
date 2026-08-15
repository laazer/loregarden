/**
 * What a container shows before its required settings are filled in.
 *
 * A pane that renders nothing is indistinguishable from a broken one, and a
 * freshly dropped container has no ticket, workspace, or URL yet — so every
 * primitive says what it is waiting for rather than going blank.
 */

import type { ReactNode } from "react";

export function Unconfigured({ children }: { children: ReactNode }) {
  return (
    <p
      style={{
        margin: 0,
        padding: 16,
        color: "var(--txl)",
        fontSize: 12.5,
        textAlign: "center",
      }}
    >
      {children}
    </p>
  );
}
