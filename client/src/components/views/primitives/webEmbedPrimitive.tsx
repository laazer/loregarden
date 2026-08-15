/**
 * An external page in a container, behind a sandbox.
 *
 * This is the app's first iframe, and it renders inside a Tauri webview — a
 * same-origin frame there reaches the app's own storage and its Tauri IPC
 * surface. So the frame gets `allow-scripts` and nothing else: no
 * `allow-same-origin`, no ambient permissions, no `srcdoc`.
 *
 * A URL the policy refuses produces no frame at all. It is shown back as text,
 * which is how an operator fixes the setting, and is never written into an
 * attribute the browser would follow.
 */

import { definePrimitive } from "./definePrimitive";
import { safeEmbedUrl } from "./embedUrl";
import { Unconfigured } from "./Unconfigured";

type WebEmbedSettings = {
  url: string;
};

/** Everything this frame is allowed to do. Adding a token needs a reason. */
const SANDBOX = "allow-scripts";

export const webEmbedPrimitive = definePrimitive<WebEmbedSettings>({
  id: "web_embed",
  displayName: "Web Embed",
  icon: "◧",
  category: "External",
  containerKind: "web_embed",
  settingsFields: [
    {
      key: "url",
      kind: "string",
      label: "URL",
      default: "",
      help: "An https page, or an http one on localhost. Anything else is refused.",
    },
  ],
  parseSettings: (raw) => ({ url: typeof raw.url === "string" ? raw.url : "" }),
  Component: ({ settings }) => {
    const src = safeEmbedUrl(settings.url);

    // An empty URL is the same "not configured yet" state every other primitive
    // has, and says so in the same words the others do.
    if (settings.url.trim() === "") {
      return <Unconfigured>Set an http or https URL for this embed.</Unconfigured>;
    }

    // A refusal is not that state: it echoes back the text that was refused, so
    // the operator can see which URL the setting actually holds.
    if (src === null) {
      return (
        <div style={{ padding: 16, color: "var(--txl)", fontSize: 12.5 }}>
          <p style={{ margin: "0 0 6px" }}>
            This URL cannot be embedded — https is allowed from any host, http only from
            localhost.
          </p>
          <code style={{ fontFamily: "var(--mono)", wordBreak: "break-all" }}>{settings.url}</code>
        </div>
      );
    }

    return (
      <iframe
        title={`Embedded page: ${src}`}
        src={src}
        sandbox={SANDBOX}
        allow=""
        referrerPolicy="no-referrer"
        style={{ width: "100%", height: "100%", minWidth: "0", minHeight: "0", border: "none" }}
      />
    );
  },
});
