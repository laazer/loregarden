import type { NavigateFunction } from "react-router-dom";

let navigateRef: NavigateFunction | null = null;

export function setRouterNavigate(navigate: NavigateFunction | null) {
  navigateRef = navigate;
}

export function getRouterNavigate(): NavigateFunction | null {
  return navigateRef;
}
