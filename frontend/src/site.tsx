import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { get } from "./api";
import type { Site } from "./types";

const SiteContext = createContext<Site | null>(null);

export function SiteProvider({ children }: { children: ReactNode }) {
  const [site, setSite] = useState<Site | null>(null);

  useEffect(() => {
    get<Site>("/site/")
      .then(setSite)
      .catch(() => {});
  }, []);

  return <SiteContext.Provider value={site}>{children}</SiteContext.Provider>;
}

export function useSite() {
  return useContext(SiteContext);
}
