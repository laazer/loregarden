import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { diskApi } from "../api/diskApi";
import { pushToast, toastActionFailed } from "../state/toastStore";

export const DISK_KEY = ["machine-disk"] as const;
const OVERVIEW_KEY = [...DISK_KEY, "overview"] as const;
/** While a scan runs the entry count is the progress bar; a second is enough. */
const SCANNING_POLL_MS = 1000;

/**
 * The disk overview (volume, scan status, last scan), polled only while a scan
 * runs, and the action that starts one.
 *
 * The tree is keyed on the last scan's finish time, so a scan finishing
 * refetches every open level once, without a manual refresh.
 */
export function useDiskOverview() {
  const queryClient = useQueryClient();
  const overview = useQuery({
    queryKey: OVERVIEW_KEY,
    queryFn: diskApi.overview,
    refetchInterval: (query) =>
      query.state.data?.status.state === "running" ? SCANNING_POLL_MS : false,
  });

  const scan = useMutation({
    mutationFn: diskApi.scan,
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: OVERVIEW_KEY }),
    onError: (error) => toastActionFailed("Start disk scan", error),
  });

  return { overview, scan };
}

export function useDiskTree(path: string, scannedAt: string | null) {
  return useQuery({
    queryKey: [...DISK_KEY, "tree", path, scannedAt],
    queryFn: () => diskApi.tree(path, 2),
    enabled: scannedAt !== null,
    placeholderData: (previous) => previous,
    retry: false,
  });
}

export function useDiskHistory(enabled: boolean) {
  return useQuery({
    queryKey: [...DISK_KEY, "history"],
    queryFn: () => diskApi.history(10),
    enabled,
  });
}

/** Added folders: each change answers with the new list, which is the overview's. */
export function useDiskRoots() {
  const queryClient = useQueryClient();
  const refresh = () =>
    void queryClient.invalidateQueries({ queryKey: OVERVIEW_KEY });
  const add = useMutation({
    mutationFn: diskApi.addRoot,
    onSuccess: (_roots, path) => {
      pushToast({
        tone: "success",
        title: "Folder added",
        message: `${path} is scanned from the next scan on.`,
      });
      refresh();
    },
  });
  const remove = useMutation({
    mutationFn: diskApi.removeRoot,
    onSuccess: refresh,
    onError: (error) => toastActionFailed("Remove folder", error),
  });
  return { add, remove };
}

/** After a cleanup: the stored tree changed on the server, so every disk read is stale. */
export function useInvalidateDisk() {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: DISK_KEY });
}
