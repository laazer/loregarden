import type { CSSProperties } from "react";

import baxterHead from "../../assets/chat/baxter-head.png";
import baxterSheet from "../../assets/chat/baxter.png";
import "./BaxterAvatar.css";

export type BaxterAvatarState = "idle" | "thinking" | "typing" | "responding";
export type BaxterAvatarVariant = "full" | "head";

export function BaxterAvatar({
  state = "idle",
  variant = "full",
  className,
  size,
  label = "Baxter",
}: {
  state?: BaxterAvatarState;
  /** `head` = cream profile badge (design chat icon). `full` = body spritesheet. */
  variant?: BaxterAvatarVariant;
  className?: string;
  size?: number;
  label?: string;
}) {
  const isHead = variant === "head";
  const style = {
    ...(isHead
      ? { "--baxter-head": `url(${baxterHead})` }
      : { "--baxter-sheet": `url(${baxterSheet})` }),
    ...(size != null
      ? {
          "--baxter-w": `${size}px`,
          "--baxter-h": `${size}px`,
        }
      : null),
  } as CSSProperties;

  return (
    <span
      className={[
        "baxter-avatar",
        isHead ? "baxter-avatar--head" : "baxter-avatar--full",
        `baxter-avatar--${state}`,
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      style={style}
      role="img"
      aria-label={label}
      data-baxter-state={state}
      data-baxter-variant={variant}
      tabIndex={!isHead && state === "idle" ? 0 : undefined}
    />
  );
}
