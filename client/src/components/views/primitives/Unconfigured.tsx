/**
 * What a container shows before its required settings are filled in.
 *
 * A pane that renders nothing is indistinguishable from a broken one, and a
 * freshly dropped container has no ticket, workspace, or URL yet — so every
 * primitive says what it is waiting for rather than going blank.
 *
 * ## Two sentences, and only one of them is the primitive's (554)
 *
 * Each primitive says what is *missing*. Where to fix it is said here, once,
 * because it is the same answer for all of them and it names a real control:
 * `PANE_SETTINGS_LABEL` is the accessible name of the pane header's settings
 * button, and both this copy and that button read it from one leaf module.
 *
 * Before 554 there was no such control, and these panes read "…in its settings"
 * while pointing at a surface that had never been built. Any future rewording of
 * the header's control has to come through this constant, so the copy cannot go
 * back to naming something that does not exist.
 */

import type { ReactNode } from "react";

import "../paneChrome.css";
import { PANE_SETTINGS_LABEL } from "../paneSettingsLabel";

export function Unconfigured({ children }: { children: ReactNode }) {
  return (
    <div className="pane-unconfigured">
      <p className="pane-unconfigured-lead">{children}</p>
      <p className="pane-unconfigured-hint">
        Open <b>{PANE_SETTINGS_LABEL}</b> in this pane&rsquo;s header to set it.
      </p>
    </div>
  );
}
