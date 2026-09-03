import { QueryClient } from "@tanstack/react-query";
import { createRouter } from "@tanstack/react-router";
import { routeTree } from "./routeTree.gen";

// ViVeSec serves one build from https://aiui.vivesec.com/<version>/ and injects
// a matching <base href>, so the prefix is only known at runtime — the router
// has to treat it as its root or every route 404s.
function routerBasepath(): string {
  if (typeof document !== "undefined") {
    try {
      // TanStack matches on an unslashed prefix, but <base href> always ends
      // in "/", so "/latest/" would never match and every route 404s.
      const path = new URL(document.baseURI).pathname.replace(/\/+$/, "");
      return path || "/";
    } catch {
      /* malformed <base href> — fall back to the build-time prefix */
    }
  }
  return import.meta.env.BASE_URL || "/";
}

export const getRouter = () => {
  const queryClient = new QueryClient();

  const router = createRouter({
    routeTree,
    context: { queryClient },
    basepath: routerBasepath(),
    scrollRestoration: true,
    defaultPreloadStaleTime: 0,
  });

  return router;
};
