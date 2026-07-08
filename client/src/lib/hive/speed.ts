/** Discrete simulation speed steps (live motion + replay). */
export const HIVE_SPEED_MULTIPLIERS = [0.5, 1, 2, 4] as const;

export type HiveSpeedMultiplier = (typeof HIVE_SPEED_MULTIPLIERS)[number];

export const DEFAULT_HIVE_SPEED_MULTIPLIER: HiveSpeedMultiplier = 1;

export const HIVE_AGENT_MOTION_MS = 850;
export const HIVE_FLYER_MOTION_MS = 1050;
export const HIVE_FLYER_FADE_MS = 1200;

export function isHiveSpeedMultiplier(value: number): value is HiveSpeedMultiplier {
  return (HIVE_SPEED_MULTIPLIERS as readonly number[]).includes(value);
}

export function hiveSpeedIndexFor(multiplier: number): number {
  const idx = HIVE_SPEED_MULTIPLIERS.indexOf(multiplier as HiveSpeedMultiplier);
  return idx >= 0 ? idx : HIVE_SPEED_MULTIPLIERS.indexOf(DEFAULT_HIVE_SPEED_MULTIPLIER);
}

export function clampHiveSpeedIndex(index: number): number {
  return Math.max(0, Math.min(HIVE_SPEED_MULTIPLIERS.length - 1, index));
}

export function hiveSpeedLabel(multiplier: HiveSpeedMultiplier): string {
  return multiplier === 1 ? "1×" : `${multiplier}×`;
}

export function hiveScaledMs(baseMs: number, multiplier: HiveSpeedMultiplier): number {
  return Math.max(80, Math.round(baseMs / multiplier));
}

export function hiveReplayFrameMs(multiplier: HiveSpeedMultiplier): number {
  return hiveScaledMs(900, multiplier);
}
