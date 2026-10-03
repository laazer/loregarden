import type { LocalInstance } from "../api/localInstancesTypes";
import { copyText } from "../lib/clipboard";
import { platform } from "../services/platform";
import { pushToast, toastActionFailed } from "../state/toastStore";
import { AddToTabItems } from "./AddToTabMenu";
import { OverflowMenu, OverflowMenuItem } from "./OverflowMenu";

/**
 * The ⋯ menu on an instance row: open its site in the browser, copy its URL,
 * or embed it in one of the operator's loregarden tabs — new or existing. An
 * exited instance has nothing listening, so it offers only the copy.
 */
export function LocalInstanceMenu({ instance }: { instance: LocalInstance }) {
  const live = instance.state !== "exited";
  return (
    <OverflowMenu label={`More actions for ${instance.name}`} align="right">
      {live && (
        <OverflowMenuItem
          onSelect={() =>
            platform.openExternal(instance.url).catch((error) => toastActionFailed("Open instance", error))
          }
        >
          Open in browser
        </OverflowMenuItem>
      )}
      <OverflowMenuItem
        onSelect={() =>
          void copyText(instance.url).then(
            () => pushToast({ tone: "success", title: "Copied", message: instance.url }),
            (error) => toastActionFailed("Copy URL", error),
          )
        }
      >
        Copy URL
      </OverflowMenuItem>
      {live && (
        <AddToTabItems primitiveId="web_embed" values={new Map([["url", instance.url]])} title={instance.name} />
      )}
    </OverflowMenu>
  );
}
