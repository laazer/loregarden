function PlayIcon() {
  return (
    <svg
      className="lg-primitive-run-icon"
      width="10"
      height="10"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden
    >
      <path d="M8 5.2v13.6L18.6 12z" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg
      className="lg-primitive-run-icon"
      width="10"
      height="10"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden
    >
      <rect x="7" y="7" width="10" height="10" rx="1" />
    </svg>
  );
}

export function PlayButton({
  label = "Play",
  disabled = false,
  onClick,
}: {
  label?: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="lg-primitive-run-btn lg-primitive-run-btn--play"
      disabled={disabled}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
    >
      <PlayIcon />
      {label}
    </button>
  );
}

export function StopButton({
  label = "Stop",
  disabled = false,
  onClick,
}: {
  label?: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="lg-primitive-run-btn lg-primitive-run-btn--stop"
      disabled={disabled}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
    >
      <StopIcon />
      {label}
    </button>
  );
}
