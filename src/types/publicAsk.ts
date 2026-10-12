export interface PublicAskGroup {
  id: string;
  name: string;
  description: string;
  repos: string[];
  enabled: boolean;
}

export interface PublicAskSettings {
  enabled: boolean;
  revision: number;
  groups: PublicAskGroup[];
}

export interface PublicAskCatalog {
  enabled: boolean;
  groups: { id: string; name: string; description: string; repo_count: number }[];
  defaultProvider?: string;
  defaultModel?: string;
  maxConcurrentRuns?: number;
}
