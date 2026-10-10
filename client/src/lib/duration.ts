/**
 * A run duration as "45s", "2m 0s" or "2h 33m".
 *
 * Rounds to whole seconds *before* splitting into minutes. Rounding the
 * remainder instead rendered a genuine 119.7s run as "1m 60s". Past an hour the
 * seconds are dropped: "153m 43s" made a reader do the division.
 */
export function duration(seconds: number | null): string {
  if (seconds === null) return "";
  const whole = Math.round(seconds);
  if (whole < 60) return `${whole}s`;
  if (whole < 3600) return `${Math.floor(whole / 60)}m ${whole % 60}s`;
  return `${Math.floor(whole / 3600)}h ${Math.floor((whole % 3600) / 60)}m`;
}
