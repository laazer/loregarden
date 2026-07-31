import { useEffect, useState } from "react";

import { type Toast, useToastStore } from "../state/toastStore";
import { IconCloseButton } from "./IconCloseButton";
import "./ToastHost.css";

const TONE_GLYPH: Record<Toast["tone"], string> = {
  error: "✕",
  warning: "⚠",
  success: "✓",
  info: "ℹ",
};

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: string) => void }) {
  const { id, duration } = toast;

  useEffect(() => {
    if (duration <= 0) return;
    const timer = setTimeout(() => onDismiss(id), duration);
    return () => clearTimeout(timer);
  }, [id, duration, onDismiss]);

  return (
    <div
      className={`toast toast--${toast.tone}`}
      role={toast.tone === "error" || toast.tone === "warning" ? "alert" : "status"}
    >
      <span className="toast__glyph" aria-hidden>
        {TONE_GLYPH[toast.tone]}
      </span>
      <div className="toast__body">
        <div className="toast__title">{toast.title}</div>
        {toast.message ? <div className="toast__message">{toast.message}</div> : null}
      </div>
      <IconCloseButton onClick={() => onDismiss(id)} aria-label={`Dismiss: ${toast.title}`} />
    </div>
  );
}

const EDGE_GAP = 20;

/**
 * Corner of the page area, not of the window.
 *
 * The utility dock is a real flex sibling — bottom edge or right edge, and its
 * size moves with the Copilot panel. Measuring the screen area keeps a toast
 * off the dock's chat composer wherever the operator has parked it, without
 * this component having to know the dock's layout rules.
 */
function useScreenAreaCorner() {
  const [corner, setCorner] = useState({ right: EDGE_GAP, bottom: EDGE_GAP });

  useEffect(() => {
    const area = document.querySelector(".screen-area");
    if (!area) return;

    const measure = () => {
      const rect = area.getBoundingClientRect();
      setCorner({
        right: Math.max(EDGE_GAP, window.innerWidth - rect.right + EDGE_GAP),
        bottom: Math.max(EDGE_GAP, window.innerHeight - rect.bottom + EDGE_GAP),
      });
    };
    measure();

    window.addEventListener("resize", measure);
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(area);
    return () => {
      window.removeEventListener("resize", measure);
      observer?.disconnect();
    };
  }, []);

  return corner;
}

/**
 * App-wide toast stack.
 *
 * Mounted once in AppLayout. Anything can push to it — including code outside
 * React, via `pushToast` — so a failed action reports itself even when the
 * component that started it has gone away.
 */
export function ToastHost() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);
  const corner = useScreenAreaCorner();

  if (toasts.length === 0) return null;

  return (
    <div className="toast-host" aria-live="polite" style={corner}>
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onDismiss={dismiss} />
      ))}
    </div>
  );
}
