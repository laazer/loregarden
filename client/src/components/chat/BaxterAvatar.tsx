import type { CSSProperties } from "react";

import baxterSheet from "../../assets/chat/baxter.png";
import "./BaxterAvatar.css";

export type BaxterAvatarState = "idle" | "thinking" | "typing" | "responding";

export function BaxterAvatar({
  state = "idle",
  className,
  size,
  label = "Baxter",
}: {
  state?: BaxterAvatarState;
  className?: string;
  size?: number;
  label?: string;
}) {
  const style = {
    "--baxter-sheet": `url(${baxterSheet})`,
    ...(size != null
      ? {
          "--baxter-w": `${size}px`,
          "--baxter-h": `${(size * 196) / 180}px`,
        }
      : null),
  } as CSSProperties;

  return (
    <span
      className={["baxter-avatar", `baxter-avatar--${state}`, className].filter(Boolean).join(" ")}
      style={style}
      role="img"
      aria-label={label}
      data-baxter-state={state}
      tabIndex={state === "idle" ? 0 : undefined}
    />
  );
}
