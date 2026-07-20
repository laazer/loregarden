/**
 * A run duration as "45s" or "2m 0s".
 *
 * Rounds to whole seconds *before* splitting into minutes. Rounding the
 * remainder instead rendered a genuine 119.7s run as "1m 60s".
 */
export function duration(seconds: number | null): string {
  if (seconds === null) return "";
  const whole = Math.round(seconds);
  if (whole < 60) return `${whole}s`;
  return `${Math.floor(whole / 60)}m ${whole % 60}s`;
}
