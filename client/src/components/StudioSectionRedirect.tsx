import { Navigate } from "react-router-dom";

import { studioPath } from "../lib/appNavigation";

export function StudioSectionRedirect() {
  return <Navigate to={studioPath("agents")} replace />;
}
