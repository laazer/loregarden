import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";

import type { BrowseListing } from "../lib/pathExplorer";
import {
  browseSeed,
  browseTargetReached,
  resolveBrowseSeed,
} from "../lib/pathExplorer";

interface UsePathBrowseOptions<T extends BrowseListing> {
  explorerKey: string;
  startPath?: string;
  navigateTo?: string;
  enabled?: boolean;
  fetchListing: (seed: string) => Promise<T>;
}

export function usePathBrowse<T extends BrowseListing>({
  explorerKey,
  startPath = ".",
  navigateTo = "",
  enabled = true,
  fetchListing,
}: UsePathBrowseOptions<T>) {
  const [browsePath, setBrowsePath] = useState<string | undefined>(undefined);

  useEffect(() => {
    const target = browseSeed(navigateTo);
    if (!target || target === ".") return;
    setBrowsePath(target);
  }, [navigateTo]);

  const seed = resolveBrowseSeed(browsePath, startPath);
  const query = useQuery({
    queryKey: ["path-browse", explorerKey, seed],
    queryFn: () => fetchListing(seed),
    enabled,
    staleTime: 0,
  });

  const data = query.data;
  const atTarget = browseTargetReached(seed, data);
  const loading = (query.isFetching || query.isPending) && !atTarget;
  const pathMismatch = Boolean(browsePath && data && !atTarget && !loading);
  const displayPath =
    browsePath ??
    data?.current_path ??
    (seed === "." ? data?.repo_root : seed) ??
    "Loading…";

  const navigate = useCallback((path: string) => {
    setBrowsePath(path);
  }, []);

  const resetBrowse = useCallback(() => {
    setBrowsePath(undefined);
  }, []);

  return {
    data,
    loading,
    pathMismatch,
    error: query.error,
    seed,
    browsePath,
    displayPath,
    navigate,
    resetBrowse,
  };
}
