import { useEffect, useRef, useState } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";

import { API_BASE } from "../api/client";
import {
  TerminalSocket,
  terminalSocketUrl,
  type TerminalEnd,
  type TerminalStatus,
} from "../lib/terminalSocket";
import "@xterm/xterm/css/xterm.css";
import "./TerminalPanel.css";

interface TerminalPanelProps {
  workspaceSlug: string;
  /** Shown in the title bar for orientation; not bound to the shell. */
  agent?: string | null;
}

/**
 * A real shell in the workspace, rendered by xterm.
 *
 * The badge says `your shell`, not `sandbox`: this runs a login shell with the
 * same privileges as the process serving the API, and labelling it a sandbox
 * would promise containment that does not exist.
 *
 * The header names the workspace, never a branch. The comp asked for
 * `▸ {agent} — {branch}`, which assumed a tty bound to a run; this shell opens
 * in the workspace root, so the ticket's branch is true only by coincidence.
 * Observed disagreeing in the dock — header saying `feat/bootstrap-ui` over a
 * prompt reading `u3d-terminal-dock`. The shell's own prompt is authoritative
 * about the branch, and it already shows it.
 */
export function TerminalPanel({ workspaceSlug, agent }: TerminalPanelProps) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState<TerminalStatus>("connecting");
  const [end, setEnd] = useState<TerminalEnd | null>(null);
  // Bumping this remounts the effect, which is what "reconnect" means here:
  // the old shell is gone, so this starts a new one rather than resuming.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    setEnd(null);
    const term = new Terminal({
      convertEol: true,
      cursorBlink: true,
      fontSize: 12,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
      theme: { background: "#0b0f14", foreground: "#c8d3df", cursor: "#7dd3fc" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(mount);

    const socket = new TerminalSocket(terminalSocketUrl(workspaceSlug, API_BASE), {
      onData: (chunk) => term.write(chunk),
      onStatus: (next) => {
        setStatus(next);
        // The first fit runs while the socket is still CONNECTING, so its size
        // goes nowhere. Re-send on open, or the shell keeps its default 80x24
        // while xterm renders a different geometry and every line wraps in the
        // wrong place.
        if (next === "open") sync();
      },
      // Only the footer states the reason. The server already writes its own
      // refusals into the stream, and echoing the close reason as well printed
      // the same sentence twice, which reads as a glitch rather than an answer.
      onEnd: setEnd,
    });

    const sync = () => {
      // fit() measures the DOM; a detached or zero-height mount makes it throw
      // rather than return, and a collapsed dock is an ordinary state here.
      try {
        fit.fit();
      } catch {
        return;
      }
      socket.resize(term.rows, term.cols);
    };

    const typed = term.onData((data) => socket.send(data));
    socket.open();
    sync();

    const observer = new ResizeObserver(sync);
    observer.observe(mount);

    return () => {
      observer.disconnect();
      typed.dispose();
      socket.close();
      term.dispose();
    };
  }, [workspaceSlug, attempt]);

  return (
    <section className="terminal-panel" aria-label={`Terminal for ${workspaceSlug}`}>
      <header className="terminal-panel-bar">
        <span className="terminal-lights" aria-hidden>
          <i className="terminal-light terminal-light--close" />
          <i className="terminal-light terminal-light--min" />
          <i className="terminal-light terminal-light--max" />
        </span>
        <span className="terminal-panel-title">
          ▸ {agent?.trim() ? `${agent.trim()} — ` : ""}
          {workspaceSlug}
        </span>
        <span
          className="terminal-panel-badge"
          title="No sandbox: this shell has the same privileges as the control plane."
        >
          pty · your shell
        </span>
        {status === "connecting" && <span className="terminal-panel-status">connecting…</span>}
      </header>

      <div className="terminal-panel-surface" ref={mountRef} />

      {end && (
        <footer className="terminal-panel-ended">
          <span>{end.reason}</span>
          <button type="button" className="btn-secondary btn-compact" onClick={() => setAttempt((n) => n + 1)}>
            Start a new shell
          </button>
        </footer>
      )}
    </section>
  );
}
