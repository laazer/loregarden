import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { setRouterNavigate } from "../lib/routerBridge";

export function RouterBridgeSync() {
  const navigate = useNavigate();

  useEffect(() => {
    setRouterNavigate(navigate);
    return () => setRouterNavigate(null);
  }, [navigate]);

  return null;
}
