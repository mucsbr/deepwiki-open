import type RepoInfo from '@/types/repoinfo';

export interface AskProps {
  repoInfo: RepoInfo;
  provider?: string;
  model?: string;
  isCustomModel?: boolean;
  customModel?: string;
  language?: string;
  onRef?: (ref: { clearConversation: () => void }) => void;
  relatedRepos?: string[];
  isGlobalAsk?: boolean;
  publicAccess?: {
    visitorKey: string | null;
    groups: { id: string; name: string }[];
    selectedGroups: string[];
    systemPrompt: string;
    onSessionSettings: (groups: string[], prompt: string) => void;
  };
}
export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
export interface AgentRun {
  id: string; session_id: string; message: string; provider: string; model: string;
  status: RunStatus; answer: string; error?: string | null;
  repos?: { project: string; url: string; commit: string }[] | null;
  activity?: { events: AgentEvent[]; cursor: number };
  kind?: 'analysis' | 'compact';
}
export interface AgentDocument { name: string; content: string; updated_at: string }
export interface AgentSession {
  id: string; title: string; repos: { project: string; url: string; commit: string }[];
  language: string; updated_at: string;
  public_groups?: string[];
  system_prompt?: string;
  active_status?: RunStatus | null;
}
export interface ContextUsage { input_tokens: number; budget_tokens: number; source: 'reported' | 'estimated'; overhead_tokens?: number }
export interface SessionDetail extends AgentSession { runs: AgentRun[]; documents: AgentDocument[]; context?: ContextUsage | null }
export interface AgentEvent {
  id: number; type: string;
  data: { content?: string; status?: RunStatus; error?: string | boolean | null;
    id?: string; name?: string; repo?: string; phase?: string; done?: boolean;
    input?: unknown; result?: Record<string, number>;
    repos?: { project: string; url: string; commit: string }[];
    input_tokens?: number; budget_tokens?: number; source?: 'reported' | 'estimated'; overhead_tokens?: number;
    manual?: boolean; before_tokens?: number; after_tokens?: number;
    todos?: { content: string; status: string }[] };
}
export interface Todo { content: string; status: string }
export type ActivityItem =
  | { kind: 'message'; id: string; content: string; done: boolean; final: boolean }
  | { kind: 'tool'; id: string; name: string; input?: unknown; result?: Record<string, number>; done: boolean; error: boolean; interrupted?: boolean }
  | { kind: 'plan'; id: string; todos: Todo[] }
  | { kind: 'refresh'; id: string; repo: string; phase: string }
  | { kind: 'compaction'; id: string; phase: string; manual: boolean; before_tokens?: number; after_tokens?: number }
  | { kind: 'status'; id: string; status: RunStatus };
export interface RunActivity { items: ActivityItem[]; cursor: number; legacyMessageId?: string; context?: ContextUsage; contextSequence?: number }
export function isActive(status?: RunStatus): boolean { return status === 'queued' || status === 'running'; }
