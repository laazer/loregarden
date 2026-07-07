const PRIO_BARS: Record<number, string[]> = {
  1: ["var(--red)", "var(--red)", "var(--red)"],
  2: ["var(--amb)", "var(--amb)", "var(--bd2)"],
  3: ["var(--txm)", "var(--bd2)", "var(--bd2)"],
};

export function PrioBars({ priority, size = "sm" }: { priority: number; size?: "sm" | "md" }) {
  const bars = PRIO_BARS[priority] ?? PRIO_BARS[3];
  const heights = size === "md" ? [8, 12, 16] : [6, 9, 12];
  return (
    <div className={`prio-bars prio-bars--${size}`} aria-hidden>
      {bars.map((color, i) => (
        <span key={i} style={{ height: heights[i], background: color }} />
      ))}
    </div>
  );
}
