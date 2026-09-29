'use client';

import Markdown from '@/components/Markdown';
import { isActive, type ActivityItem, type AgentRun, type Todo } from './types';

type Translate = (key: string, fallback: string) => string;

export function PlanList({ todos, t }: { todos: Todo[]; t: Translate }) {
  return <ul className="mt-2 space-y-2">{todos.map((todo, index) => <li key={index} className="flex gap-2">
    <span aria-hidden="true" className={todo.status === 'in_progress' ? 'text-[var(--accent-primary)]' : 'text-[var(--muted)]'}>{todo.status === 'completed' ? '✓' : todo.status === 'in_progress' ? '◉' : '○'}</span>
    <span className={todo.status === 'completed' ? 'text-[var(--muted)] line-through' : ''}>
      <span className="sr-only">{t(`todo_${todo.status}`, todo.status)}: </span>{todo.content}
    </span>
  </li>)}</ul>;
}

function ToolActivity({ item, running, t }: { item: Extract<ActivityItem, { kind: 'tool' }>; running: boolean; t: Translate }) {
  running = running && !item.interrupted;
  const input = item.input && typeof item.input === 'object' ? item.input as Record<string, unknown> : {};
  const context = ['repo', 'path', 'query', 'pattern', 'name', 'page_id']
    .flatMap(key => typeof input[key] === 'string' && input[key] ? [String(input[key])] : []);
  if (typeof input.start_line === 'number') context.push(`L${input.start_line}${typeof input.end_line === 'number' ? `–${input.end_line}` : ''}`);
  const status = item.error ? t('toolFailed', 'Tool failed') : item.done ? t('completed', 'Completed')
    : running ? t('running', 'Running') : t('interrupted', 'Interrupted');
  return <details className="rounded-lg border border-[var(--border-color)] bg-[var(--background)]/50 px-3 py-2 text-xs">
    <summary className="cursor-pointer list-none">
      <span className="flex items-start gap-2">
        <span aria-hidden="true" className={item.error ? 'text-red-500' : !item.done && running ? 'animate-pulse text-[var(--accent-primary)]' : 'text-[var(--muted)]'}>
          {item.error ? '!' : item.done ? '✓' : !running ? '−' : '◌'}
        </span>
        <span className="min-w-0 flex-1">
          <span className="font-mono font-medium">{item.name}</span>
          {context.length > 0 && <span title={context.join(' · ')} className="mt-1 line-clamp-2 break-words font-mono text-[var(--muted)] [overflow-wrap:anywhere]">{context.join(' · ')}</span>}
        </span>
        <span className="shrink-0 text-[var(--muted)]">{status} <span aria-hidden="true">⌄</span></span>
      </span>
    </summary>
    <div className="mt-3 space-y-2 border-t border-[var(--border-color)] pt-2">
      <p className="font-medium">{t('toolInput', 'Parameters')}</p>
      <pre className="max-h-52 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(item.input ?? {}, null, 2)}</pre>
      {item.result && Object.keys(item.result).length > 0 && <>
        <p className="font-medium">{t('toolResult', 'Result counts')}</p>
        <pre className="whitespace-pre-wrap">{JSON.stringify(item.result, null, 2)}</pre>
      </>}
      {item.error && <p className="text-red-500">{t('toolErrorHint', 'The tool returned an error. The agent can use another approach.')}</p>}
    </div>
  </details>;
}

export default function RunTimeline({ run, items, t }: { run: AgentRun; items: ActivityItem[]; t: Translate }) {
  const running = isActive(run.status);
  const latestPlan = items.findLast(item => item.kind === 'plan');
  const hasAnswer = items.some(item => item.kind === 'message' && item.content === run.answer);
  return <div className="space-y-3" aria-label={t('progress', 'Analysis activity')}>
    {items.map(item => {
      if (item.kind === 'message') return item.content && <div key={item.id} className="px-1 [overflow-wrap:anywhere]">
        <p className="mb-1 text-xs font-medium text-[var(--muted)]">{item.final ? t('answer', 'Answer') : t('update', 'Progress update')}</p>
        <Markdown content={item.content} allowHtml={false} />
      </div>;
      if (item.kind === 'tool') return <ToolActivity key={item.id} item={item} running={running} t={t} />;
      if (item.kind === 'plan') return <details key={item.id} open={!running && item.id === latestPlan?.id}
        className="rounded-lg border border-[var(--accent-primary)]/25 bg-[var(--accent-primary)]/5 px-3 py-2 text-sm">
        <summary className="cursor-pointer font-medium">{t('plan', 'Plan')} · {item.todos.filter(todo => todo.status === 'completed').length}/{item.todos.length}</summary>
        <PlanList todos={item.todos} t={t} />
      </details>;
      if (item.kind === 'refresh') return <p key={item.id} className="break-words text-xs text-[var(--muted)]">
        {item.phase === 'ready' ? '✓' : '↻'} {item.repo} · {item.phase === 'ready' ? t('codeReady', 'Code and index ready')
          : item.phase === 'updating' ? t('updatingCode', 'Updating code and index') : t('checkingCode', 'Checking GitLab for new code')}
      </p>;
      if (item.kind === 'status' && !isActive(item.status)) return <p key={item.id} className="text-xs text-[var(--muted)]">{t(item.status, item.status)}</p>;
      return null;
    })}
    {run.answer && !hasAnswer && <div className="px-1"><Markdown content={run.answer} allowHtml={false} /></div>}
    {running && <p role="status" className="flex items-center gap-2 px-1 text-xs text-[var(--muted)]">
      <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--accent-primary)]" />{t('working', 'Working · activity will appear here as it happens')}
    </p>}
  </div>;
}
