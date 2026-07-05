import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Dashboard } from "./pages/Dashboard";
import { EditorPage } from "./pages/EditorPage";
import { StudioPage } from "./pages/StudioPage";
import { useUiStore } from "./state/uiStore";
import "./index.css";

const queryClient = new QueryClient();

function AppShell() {
  const appPage = useUiStore((s) => s.appPage);
  return appPage === "studio" ? <StudioPage /> : appPage === "editor" ? <EditorPage /> : <Dashboard />;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppShell />
    </QueryClientProvider>
  );
}
