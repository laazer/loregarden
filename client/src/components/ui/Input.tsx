/**
 * The app's <input>, painted from the theme tokens.
 *
 * Text-like types take the field floor from index.css. Checkboxes, radios and
 * ranges keep their native shape — repainting those replaces the control — and
 * take the accent colour instead of the browser's blue.
 */

import type { ComponentPropsWithRef } from "react";

import { joinClasses } from "./classNames";
import "./primitives.css";

const CHECKABLE_TYPES = new Set(["checkbox", "radio", "range"]);

export type InputProps = ComponentPropsWithRef<"input">;

export function Input({ className, type, ...rest }: InputProps) {
  const checkable = type !== undefined && CHECKABLE_TYPES.has(type);
  return (
    <input type={type} className={joinClasses(checkable && "ui-check", className)} {...rest} />
  );
}
