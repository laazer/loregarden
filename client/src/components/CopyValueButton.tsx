import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { copyText } from '../lib/clipboard';
import { describeError, pushToast } from '../state/toastStore';

const COPIED_MS = 1500;

export interface CopyValueButtonProps {
  value: string;
  /** What is being copied, for the accessible name: "ticket number" → "Copy ticket number". */
  what: string;
  className?: string;
}

/** A small icon button that copies `value` and says so for a moment. */
export function CopyValueButton({ value, what, className = '' }: CopyValueButtonProps) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  const handleCopy = async (event: MouseEvent) => {
    // The copy targets sit inside a header a parent may treat as clickable.
    event.stopPropagation();
    try {
      await copyText(value);
    } catch (error) {
      pushToast({ tone: 'error', title: `Couldn't copy ${what}`, message: describeError(error, 'Clipboard unavailable') });
      return;
    }
    setCopied(true);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), COPIED_MS);
  };

  const label = copied ? `Copied ${what}` : `Copy ${what}`;
  return (
    <button
      type="button"
      className={`copy-value-btn${copied ? ' is-copied' : ''}${className ? ` ${className}` : ''}`}
      aria-label={label}
      title={label}
      disabled={!value}
      onClick={(event) => void handleCopy(event)}
    >
      {copied ? (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden>
          <path d="M20 6 9 17l-5-5" />
        </svg>
      ) : (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <rect x="9" y="9" width="12" height="12" rx="2" />
          <path d="M5 15V5a2 2 0 0 1 2-2h10" />
        </svg>
      )}
    </button>
  );
}
