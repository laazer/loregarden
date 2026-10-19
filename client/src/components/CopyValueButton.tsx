import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react';
import { copyText } from '../lib/clipboard';
import { describeError, pushToast } from '../state/toastStore';
import { Button } from './ui/Button';
import './CopyValueButton.css';

const COPIED_MS = 1500;

export interface CopyValueButtonProps {
  value: string;
  /** What is being copied, for the accessible name: "ticket number" → "Copy ticket number". */
  what: string;
  className?: string;
  /**
   * The button's face instead of the copy icon, usually the value itself, so
   * the id a person reads is the thing they click. A tick follows it once copied.
   */
  children?: ReactNode;
}

const COPY_ICON = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
    <rect x="9" y="9" width="12" height="12" rx="2" />
    <path d="M5 15V5a2 2 0 0 1 2-2h10" />
  </svg>
);

const COPIED_ICON = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden>
    <path d="M20 6 9 17l-5-5" />
  </svg>
);

/** A small button that copies `value` and says so for a moment. */
export function CopyValueButton({ value, what, className = '', children }: CopyValueButtonProps) {
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
    <Button
      variant="plain"
      className={`copy-value-btn${children ? ' copy-value-btn--text' : ''}${copied ? ' is-copied' : ''}${className ? ` ${className}` : ''}`}
      aria-label={label}
      title={label}
      disabled={!value}
      onClick={(event) => void handleCopy(event)}
    >
      {children ? (
        <>
          <span className="copy-value-text">{children}</span>
          {copied ? COPIED_ICON : null}
        </>
      ) : copied ? (
        COPIED_ICON
      ) : (
        COPY_ICON
      )}
    </Button>
  );
}
