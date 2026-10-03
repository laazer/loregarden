import type { FilePickerOptions, NotifyOptions, PlatformAdapter } from "./types";

async function ensureNotificationPermission(): Promise<boolean> {
  if (typeof Notification === "undefined") return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;
  const permission = await Notification.requestPermission();
  return permission === "granted";
}

export const webPlatform: PlatformAdapter = {
  isDesktop: false,

  async openFile(_options?: FilePickerOptions) {
    // Browsers cannot resolve a real filesystem path for security reasons —
    // this app's actual file browsing already goes through the in-app
    // workspace/editor browser backed by the API, so this is a documented
    // no-op rather than a misleading partial implementation.
    console.warn("platform.openFile() is a desktop-only native dialog; not available in the browser.");
    return null;
  },

  async saveFile(_options?: FilePickerOptions) {
    console.warn("platform.saveFile() is a desktop-only native dialog; not available in the browser.");
    return null;
  },

  async notify(options: NotifyOptions) {
    const granted = await ensureNotificationPermission();
    if (!granted) {
      console.warn("Notification permission was not granted; skipping notify()", options);
      return;
    }
    new Notification(options.title, { body: options.body });
  },

  async clipboardRead() {
    return navigator.clipboard.readText();
  },

  async clipboardWrite(text: string) {
    await navigator.clipboard.writeText(text);
  },

  async openExternal(url: string) {
    window.open(url, "_blank", "noopener,noreferrer");
  },
};
