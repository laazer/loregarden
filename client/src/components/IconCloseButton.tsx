import type { ButtonHTMLAttributes } from "react";

export function IconCloseButton({
  className,
  title = "Close",
  "aria-label": ariaLabel = "Close",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={`icon-close-btn${className ? ` ${className}` : ""}`}
      title={title}
      aria-label={ariaLabel}
      {...props}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <path d="M18 6 6 18M6 6l12 12" />
      </svg>
    </button>
  );
}
