/**
 * The embed's frame, plus the placeholder that covers it until it paints.
 *
 * Its own module rather than a function inside `webEmbedPrimitive`, because a
 * file that exports both a primitive record and a component is a file fast
 * refresh cannot reload. The policy that decides *whether* there is a frame
 * stays in `embedUrl`; this only draws the one it is handed.
 */

import { useState } from "react";

import { platform } from "../../../services/platform";
import { PaneSkeleton } from "../../ui/PaneSkeleton";
import "./webEmbed.css";

/** Everything this frame is allowed to do. Adding a token needs a reason. */
const SANDBOX = "allow-scripts";

/**
 * An iframe that has not loaded is a white rectangle — brighter than anything
 * else in the app, and indistinguishable from a page that loaded blank. The
 * skeleton sits *over* the frame rather than replacing it, because a frame that
 * is not mounted never starts loading and so never fires `load`.
 *
 * `load` fires for the frame's own error page too, which is the behaviour we
 * want: the placeholder's job is to end when the browser is done, not to judge
 * what it fetched. Nothing here reads across the origin boundary.
 *
 * ## Why there is always a way out of the frame
 *
 * Many sites refuse to be embedded at all — `google.com` sends
 * `x-frame-options: SAMEORIGIN`, and the browser then renders its own refusal
 * where the page would be. That is the site's decision and no sandbox token or
 * CSP of ours can override it.
 *
 * Whether a given frame was refused is not knowable from here: the origin
 * boundary that hides a page that loaded hides one that did not, and the
 * signals that leak differ by browser. So the bar tells the operator the URL
 * and offers to open it outside the app — which is the answer whether the frame
 * is refused, slow, or fine, and needs no guess about which.
 *
 * The one thing we can fix without knowing is how the refusal *looks*: the page
 * the browser draws in a refused frame is its own, and it follows the frame's
 * `color-scheme`. Left at the default it is a white sheet in a dark app, which
 * reads as a rendering fault rather than as a site declining. Declaring `dark`
 * gets the browser's own dark error page instead.
 */
export function WebEmbedFrame({ src }: { src: string }) {
  const [loaded, setLoaded] = useState(false);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        flex: "1 1 auto",
        minWidth: 0,
        minHeight: 0,
      }}
    >
      <div className="web-embed-bar">
        <span className="web-embed-url" title={src}>
          {src}
        </span>
        <button
          type="button"
          className="web-embed-open"
          onClick={() => void platform.openExternal(src)}
        >
          Open outside
        </button>
      </div>
      <div
        style={{
          position: "relative",
          display: "flex",
          flex: "1 1 auto",
          minWidth: 0,
          minHeight: 0,
        }}
      >
      <iframe
        title={`Embedded page: ${src}`}
        src={src}
        sandbox={SANDBOX}
        allow=""
        referrerPolicy="no-referrer"
        onLoad={() => setLoaded(true)}
        style={{
          width: "100%",
          height: "100%",
          minWidth: "0",
          minHeight: "0",
          border: "none",
          colorScheme: "dark",
        }}
      />
        {loaded ? null : (
          <div style={{ position: "absolute", inset: 0, display: "flex", background: "var(--bg1)" }}>
            <PaneSkeleton variant="block" label={`Loading ${src}…`} />
          </div>
        )}
      </div>
    </div>
  );
}
