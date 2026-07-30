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
}

/**
 * A real shell in the workspace, rendered by xterm.
 *
 * Tabs and split controls live in `TerminalWorkspace`; this component owns
 * exactly one xterm instance and one websocket-backed shell.
 */
export function TerminalPanel({ workspaceSlug }: TerminalPanelProps) {
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
      // Never fit against a surface the browser has not sized. fit() happily
      // turns a 0px box into a 2-column terminal and xterm keeps it, so every
      // line wraps after two characters — and nothing re-measures to undo it.
      const { width, height } = mount.getBoundingClientRect();
      if (width < 1 || height < 1) return;
      // fit() measures the DOM; a detached mount makes it throw rather than
      // return, and a collapsed dock is an ordinary state here.
      try {
        fit.fit();
      } catch {
        return;
      }
      socket.resize(term.rows, term.cols);
    };

    const typed = term.onData((data) => socket.send(data));
    socket.open();
    // Fit on the next frame, not in this one. Measuring in the same tick the
    // mount is created reads a box the browser has not laid out yet: on first
    // paint with the dock already open that produced a 2-column terminal that
    // never recovered, because only a remount would measure again.
    const firstFit = requestAnimationFrame(sync);

    const observer = new ResizeObserver(sync);
    observer.observe(mount);

    return () => {
      cancelAnimationFrame(firstFit);
      observer.disconnect();
      typed.dispose();
      socket.close();
      term.dispose();
    };
  }, [workspaceSlug, attempt]);

  return (
    <section className="terminal-panel" aria-label={`Terminal for ${workspaceSlug}`}>
      <div className="terminal-panel-surface" ref={mountRef} />
      {status === "connecting" && (
        <span className="terminal-panel-status" role="status">
          connecting…
        </span>
      )}

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
