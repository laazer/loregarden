export function TreeExpandChevron({ expanded }: { expanded: boolean }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {expanded ? <path d="m6 9 6 6 6-6" /> : <path d="m9 18 6-6-6-6" />}
    </svg>
  );
}
