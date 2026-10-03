import { open, save } from "@tauri-apps/plugin-dialog";
import {
  readText as clipboardReadText,
  writeText as clipboardWriteText,
} from "@tauri-apps/plugin-clipboard-manager";
import {
  isPermissionGranted,
  requestPermission,
  sendNotification,
} from "@tauri-apps/plugin-notification";
import { openUrl } from "@tauri-apps/plugin-opener";

import type { FilePickerOptions, NotifyOptions, PlatformAdapter } from "./types";

async function ensureNotificationPermission(): Promise<boolean> {
  if (await isPermissionGranted()) return true;
  const permission = await requestPermission();
  return permission === "granted";
}

export const tauriPlatform: PlatformAdapter = {
  isDesktop: true,

  async openFile(options) {
    const result = await open({
      title: options?.title,
      filters: options?.filters,
      defaultPath: options?.defaultPath,
      multiple: false,
      directory: false,
    });
    return typeof result === "string" ? result : null;
  },

  async saveFile(options) {
    const result = await save({
      title: options?.title,
      filters: options?.filters,
      defaultPath: options?.defaultPath,
    });
    return result ?? null;
  },

  async notify(options: NotifyOptions) {
    const granted = await ensureNotificationPermission();
    if (!granted) {
      console.warn("Notification permission was not granted; skipping notify()", options);
      return;
    }
    sendNotification({ title: options.title, body: options.body });
  },

  async clipboardRead() {
    return (await clipboardReadText()) ?? "";
  },

  async clipboardWrite(text: string) {
    await clipboardWriteText(text);
  },

  async openExternal(url: string) {
    await openUrl(url);
  },
};

export type { FilePickerOptions };
