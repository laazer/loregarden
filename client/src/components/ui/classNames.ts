/** Join the class names that are set; `false`, `null`, `undefined` and "" drop out. */
export function joinClasses(...names: Array<string | false | null | undefined>): string {
  return names.filter(Boolean).join(" ");
}
