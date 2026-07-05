export interface BrowseListing {
  current_path: string;
  repo_path: string;
  parent_path?: string | null;
  parent_repo_path?: string | null;
  repo_root?: string;
}

export function sanitizeBrowsePath(value: string): string {
  return value.trim().replace(/\\(.)/g, "$1");
}

export function browseSeed(value: string): string {
  const trimmed = sanitizeBrowsePath(value);
  if (!trimmed || trimmed === ".") return ".";
  if (trimmed.toLowerCase().startsWith("sqlite:///")) return ".";
  return trimmed;
}

export function pathsEqual(a: string, b: string): boolean {
  return a.replace(/\/$/, "") === b.replace(/\/$/, "");
}

export function resolveBrowseSeed(browsePath: string | undefined, startPath: string): string {
  return browsePath ?? browseSeed(startPath || ".");
}

export function browseTargetReached(
  seed: string,
  data: BrowseListing | undefined,
): boolean {
  if (!data) return false;
  if (seed === ".") return data.repo_path === ".";
  return Boolean(data.current_path) && pathsEqual(data.current_path, seed);
}
