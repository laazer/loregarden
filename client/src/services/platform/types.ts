export interface FilePickerOptions {
  title?: string;
  /** e.g. [{ name: "Markdown", extensions: ["md"] }] */
  filters?: { name: string; extensions: string[] }[];
  defaultPath?: string;
}

export interface NotifyOptions {
  title: string;
  body?: string;
}

/**
 * Everything the React app needs from the host environment, behind one
 * interface. Callers never import `@tauri-apps/*` or browser APIs directly —
 * `index.ts` picks the Tauri- or browser-backed implementation once, at
 * module load, based on which environment is running.
 *
 * openFile/saveFile are native path pickers only, not content readers —
 * Loregarden's actual file I/O already goes through the FastAPI
 * `/api/.../editor` and `/api/system/browse*` endpoints, so these just hand
 * back a filesystem path (or null on cancel) for the caller to pass along.
 */
export interface PlatformAdapter {
  readonly isDesktop: boolean;
  openFile(options?: FilePickerOptions): Promise<string | null>;
  saveFile(options?: FilePickerOptions): Promise<string | null>;
  notify(options: NotifyOptions): Promise<void>;
  clipboardRead(): Promise<string>;
  clipboardWrite(text: string): Promise<void>;
  openExternal(url: string): Promise<void>;
}
