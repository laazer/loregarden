/**
 * The app's button: a <button> whose colours come from the theme tokens.
 *
 * `variant` is required because each one paints differently and a default would
 * be a silent choice: `primary` and `secondary` are the house buttons, and
 * `plain` clears the browser's light-grey bevel so a caller's own class (an icon
 * button, a tab) paints it from scratch.
 *
 * `type` defaults to "button". The browser's default is "submit", which turns
 * any button inside a form into one that submits it.
 */

import type { ComponentPropsWithRef } from "react";

import { joinClasses } from "./classNames";
import "./primitives.css";

export type ButtonVariant = "primary" | "secondary" | "plain";

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  primary: "btn-primary",
  secondary: "btn-secondary",
  plain: "ui-button-plain",
};

export interface ButtonProps extends ComponentPropsWithRef<"button"> {
  variant: ButtonVariant;
  /** The dense toolbar size (`.btn-compact`). */
  compact?: boolean;
}

export function Button({
  variant,
  compact = false,
  type = "button",
  className,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={joinClasses(VARIANT_CLASS[variant], compact && "btn-compact", className)}
      {...rest}
    />
  );
}
