import { TOOL_OPTIONS } from "../lib/appTopbarConfig";
import { useUiStore } from "../state/uiStore";
import { TopbarDropdown, TopbarDropdownItem } from "./TopbarDropdown";

export function AppTopbarToolMenu() {
  const appPage = useUiStore((s) => s.appPage);
  const setAppPage = useUiStore((s) => s.setAppPage);
  const activeToolLabel = TOOL_OPTIONS.find((tool) => tool.page === appPage)?.label ?? "IDE";

  return (
    <TopbarDropdown label={activeToolLabel} align="right">
      {TOOL_OPTIONS.map((tool) => (
        <TopbarDropdownItem
          key={tool.page}
          active={appPage === tool.page}
          onSelect={() => setAppPage(tool.page)}
        >
          {tool.label}
        </TopbarDropdownItem>
      ))}
    </TopbarDropdown>
  );
}
