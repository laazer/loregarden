import type { SelectedImportFile } from "../components/ImportTicketFileExplorer";

export function selectedImportFileList(selectedFiles: Map<string, SelectedImportFile>): SelectedImportFile[] {
  return Array.from(selectedFiles.values()).sort((a, b) => a.repo_path.localeCompare(b.repo_path));
}
