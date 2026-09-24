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
}
export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
export interface AgentRun {
  id: string; session_id: string; message: string; provider: string; model: string;
  status: RunStatus; answer: string; error?: string | null;
  repos?: { project: string; url: string; commit: string }[] | null;
}
export interface AgentDocument { name: string; content: string; updated_at: string }
export interface AgentSession {
  id: string; title: string; repos: { project: string; url: string; commit: string }[];
  language: string; updated_at: string;
}
export interface SessionDetail extends AgentSession { runs: AgentRun[]; documents: AgentDocument[] }
export interface AgentEvent {
  id: number; type: string;
  data: { content?: string; status?: RunStatus; error?: string | boolean | null;
    id?: string; name?: string; repo?: string; phase?: string;
    repos?: { project: string; url: string; commit: string }[];
    todos?: { content: string; status: string }[] };
}
export interface ToolProgress { id: string; name: string; done: boolean; error: boolean }
export function isActive(status?: RunStatus): boolean { return status === 'queued' || status === 'running'; }
