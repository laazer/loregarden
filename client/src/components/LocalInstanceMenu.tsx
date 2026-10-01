import type { LocalInstance } from "../api/localInstancesTypes";
import { copyText } from "../lib/clipboard";
import { platform } from "../services/platform";
import { pushToast, toastActionFailed } from "../state/toastStore";
import { OverflowMenu, OverflowMenuItem } from "./OverflowMenu";

/** One tab per instance: reopening it lands on the tab already showing it. */
function instanceTabName(instance: LocalInstance): string {
  return `loregarden-instance-${instance.id}`;
}

function open(action: Promise<void>) {
  action.catch((error) => toastActionFailed("Open instance", error));
}

/**
 * The ⋯ menu on an instance row: open its site — in the tab it was opened in
 * before, or a new one — and copy its URL. An exited instance has nothing
 * listening, so it offers only the copy.
 */
export function LocalInstanceMenu({ instance }: { instance: LocalInstance }) {
  const live = instance.state !== "exited";
  return (
    <OverflowMenu label={`More actions for ${instance.name}`} align="right">
      {live && platform.reusesTabs && (
        <>
          <OverflowMenuItem
            title="Reuses the tab this instance was last opened in, or opens one"
            onSelect={() => open(platform.openInTab(instance.url, instanceTabName(instance)))}
          >
            Open in existing tab
          </OverflowMenuItem>
          <OverflowMenuItem onSelect={() => open(platform.openExternal(instance.url))}>Open in new tab</OverflowMenuItem>
        </>
      )}
      {live && !platform.reusesTabs && (
        <OverflowMenuItem
          title="The desktop app hands links to your browser, which decides the tab"
          onSelect={() => open(platform.openExternal(instance.url))}
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
    </OverflowMenu>
  );
}
