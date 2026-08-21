/**
 * The embed's frame, plus the placeholder that covers it until it paints.
 *
 * Its own module rather than a function inside `webEmbedPrimitive`, because a
 * file that exports both a primitive record and a component is a file fast
 * refresh cannot reload. The policy that decides *whether* there is a frame
 * stays in `embedUrl`; this only draws the one it is handed.
 */

import { useState } from "react";

import { PaneSkeleton } from "../../ui/PaneSkeleton";

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
 */
export function WebEmbedFrame({ src }: { src: string }) {
  const [loaded, setLoaded] = useState(false);

  return (
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
        style={{ width: "100%", height: "100%", minWidth: "0", minHeight: "0", border: "none" }}
      />
      {loaded ? null : (
        <div style={{ position: "absolute", inset: 0, display: "flex", background: "var(--bg1)" }}>
          <PaneSkeleton variant="block" label={`Loading ${src}…`} />
        </div>
      )}
    </div>
  );
}
