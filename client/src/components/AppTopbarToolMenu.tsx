import { useAppPage, navigateToPage } from "../lib/useAppNavigation";
import { TOOL_OPTIONS } from "../lib/appTopbarConfig";
import { TopbarDropdown, TopbarDropdownItem } from "./TopbarDropdown";

export function AppTopbarToolMenu() {
  const appPage = useAppPage();
  const activeToolLabel = TOOL_OPTIONS.find((tool) => tool.page === appPage)?.label ?? "IDE";

  return (
    <TopbarDropdown label={activeToolLabel} align="right">
      {TOOL_OPTIONS.map((tool) => (
        <TopbarDropdownItem
          key={tool.page}
          active={appPage === tool.page}
          onSelect={() => navigateToPage(tool.page)}
        >
          {tool.label}
        </TopbarDropdownItem>
      ))}
    </TopbarDropdown>
  );
}
