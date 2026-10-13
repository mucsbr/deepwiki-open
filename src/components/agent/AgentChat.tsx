'use client';

import { useEffect, useMemo, useState } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import { useLanguage } from '@/contexts/LanguageContext';
import { useAgentConversation } from '@/hooks/useAgentConversation';
import { useChatScroll } from '@/hooks/useChatScroll';
import getRepoUrl from '@/utils/getRepoUrl';
import { agentRequest } from '@/utils/agentClient';
import Markdown from '@/components/Markdown';
import ModelSelectionModal from '@/components/ModelSelectionModal';
import RunTimeline, { PlanList } from './RunTimeline';
import { isActive, type AskProps } from './types';

const normalize = (repo: string) => {
  const path = repo.includes('://') ? new URL(repo).pathname : repo;
  return decodeURIComponent(path).replace(/^\/+|\/+$/g, '').replace(/\.git$/, '');
};

export default function AgentChat({ repoInfo, provider = '', model = '', isCustomModel = false,
  customModel = '', language = 'en', onRef, relatedRepos = [], isGlobalAsk = false, publicAccess }: AskProps) {
  const { token } = useAuth();
  const { messages } = useLanguage();
  const t = (key: string, fallback: string): string => messages.agent?.[key] || fallback;
  const publicMode = !!publicAccess;
  const privateToken = publicMode ? null : token;
  const visitorKey = publicAccess?.visitorKey;
  const selectedGroups = publicAccess?.selectedGroups;
  const systemPrompt = publicAccess?.systemPrompt ?? '';
  const auth = useMemo(() => publicMode
    ? (visitorKey ? { visitorKey } : null)
    : privateToken, [privateToken, publicMode, visitorKey]);
  const publicSettings = useMemo(() => publicMode
    ? { groups: selectedGroups ?? [], systemPrompt }
    : undefined, [selectedGroups, systemPrompt, publicMode]);
  const chat = useAgentConversation(auth, publicSettings);
  const [question, setQuestion] = useState('');
  const [selectedProvider, setProvider] = useState(provider);
  const [selectedModel, setModel] = useState(model);
  const [custom, setCustom] = useState(isCustomModel);
  const [customValue, setCustomValue] = useState(customModel);
  const [modelOpen, setModelOpen] = useState(false);
  const [comprehensive, setComprehensive] = useState(true);
  const [activeDocument, setActiveDocument] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const primary = isGlobalAsk ? '' : getRepoUrl(repoInfo);
  const scopeKey = JSON.stringify([...new Set([primary, ...relatedRepos].filter(Boolean))].sort());
  const repos: string[] = useMemo(() => JSON.parse(scopeKey), [scopeKey]);
  const lastRun = chat.session?.runs.at(-1);
  const activeRun = chat.session?.runs.findLast(run => isActive(run.status));
  const latestAnalysis = chat.session?.runs.findLast(run => run.kind !== 'compact');
  const { transcript, transcriptContent, following, jumpToLatest } = useChatScroll(`${chat.session?.id ?? ''}/${activeRun?.id ?? lastRun?.id ?? ''}`);
  const running = !!activeRun;
  const currentPlan = activeRun && chat.activities[activeRun.id]?.items.findLast(item => item.kind === 'plan');
  // A resumed analysis may predate a compaction run: compare event sequence,
  // not run creation order, when choosing the newest context measurement.
  const measured = Object.values(chat.activities).filter(activity => activity.context)
    .sort((a, b) => (b.contextSequence ?? 0) - (a.contextSequence ?? 0))[0];
  const context = measured?.context ?? chat.session?.context;
  const scopeChanged = !!chat.session && (publicAccess
    ? JSON.stringify([...(chat.session.public_groups ?? [])].sort()) !== JSON.stringify([...publicAccess.selectedGroups].sort())
    : JSON.stringify(chat.session.repos.map(repo => repo.project).sort()) !== JSON.stringify([...new Set(repos.map(normalize))].sort()));
  const hasSelection = publicAccess ? publicAccess.selectedGroups.length > 0 : repos.length > 0;
  const document = chat.session?.documents.find(item => item.name === activeDocument);

  useEffect(() => { onRef?.({ clearConversation: chat.reset }); }, [onRef, chat.reset]);
  const onSessionSettings = publicAccess?.onSessionSettings;
  const sessionId = chat.session?.id;
  const sessionPrompt = chat.session?.system_prompt ?? '';
  const sessionGroupsKey = JSON.stringify(chat.session?.public_groups ?? []);
  useEffect(() => {
    if (sessionId && onSessionSettings) onSessionSettings(JSON.parse(sessionGroupsKey), sessionPrompt);
  }, [sessionId, sessionPrompt, sessionGroupsKey, onSessionSettings]);
  useEffect(() => {
    setProvider(provider); setModel(model); setCustom(isCustomModel); setCustomValue(customModel);
  }, [provider, model, isCustomModel, customModel]);
  useEffect(() => {
    if ((provider && model) || !auth) return;
    let live = true;
    agentRequest<{ defaultProvider: string; defaultModel: string }>(auth, '/config').then(data => {
      if (!live) return;
      setProvider(data.defaultProvider || '');
      setModel(data.defaultModel || '');
    }).catch(() => {});
    return () => { live = false; };
  }, [provider, model, auth]);

  const newConversation = () => { chat.reset(); setQuestion(''); setActiveDocument(''); };
  const send = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!question.trim() || running || (!hasSelection && !chat.session)) return;
    const accepted = await chat.send(question, repos, language, selectedProvider, custom ? customValue : selectedModel);
    if (accepted) setQuestion('');
  };

  return <section aria-label={t('title', 'Code analysis agent')} className="space-y-4 p-3 sm:p-4">
    <header className="flex flex-wrap items-center justify-between gap-2">
      <div>
        <h2 className="font-semibold text-[var(--foreground)]">{t('title', 'Code analysis agent')}</h2>
        <p className="text-xs text-[var(--muted)]">{t('subtitle', 'Locate features, explain rules, trace flows and create documents.')}</p>
      </div>
      <div className="flex gap-2 text-xs">
        <button className="btn-apple-secondary px-3 py-2" onClick={() => setHistoryOpen(value => !value)}>{t('history', 'History')}</button>
        <button className="btn-apple-secondary px-3 py-2" onClick={newConversation} disabled={chat.pending}>{t('newChat', 'New conversation')}</button>
        {!publicAccess && <button className="btn-apple-secondary px-3 py-2" onClick={() => setModelOpen(true)} disabled={running}>{t('model', 'Model')}</button>}
      </div>
    </header>

    {historyOpen && <nav aria-label={t('history', 'History')} className="max-h-52 overflow-y-auto rounded-xl border border-[var(--border-color)] p-2 space-y-1">
      {chat.sessions.length === 0 && <p className="p-2 text-sm text-[var(--muted)]">{t('noHistory', 'No conversations yet.')}</p>}
      {chat.sessions.map(item => <button key={item.id} onClick={() => { void chat.open(item.id); setActiveDocument(''); setHistoryOpen(false); }}
        className={`block w-full rounded-lg p-2 text-left text-sm hover:bg-[var(--background)] ${chat.session?.id === item.id ? 'text-[var(--accent-primary)]' : ''}`}>
        <span className="block truncate">{item.title || t('newChat', 'New conversation')}</span>
        {item.active_status && <span className="mr-2 text-xs text-[var(--accent-primary)]">{t(item.active_status, item.active_status)}</span>}
        <span className="text-xs text-[var(--muted)]">{item.repos.length} {t('repositories', 'repositories')} · {new Date(item.updated_at).toLocaleString()}</span>
      </button>)}
      {chat.nextOffset !== null && <button className="p-2 text-sm" onClick={() => void chat.refreshHistory(chat.nextOffset ?? 0)}>{t('loadMore', 'Load more')}</button>}
    </nav>}

    {chat.session && <details className="text-xs text-[var(--muted)]">
      <summary className="cursor-pointer">{t('scope', 'Source snapshot')} · {chat.session.repos.length} {t('repositories', 'repositories')}</summary>
      <ul className="mt-2 space-y-1">{chat.session.repos.map(repo => <li key={repo.project}>{repo.project} <code>{repo.commit.slice(0, 12)}</code></li>)}</ul>
    </details>}
    {scopeChanged && <p role="status" className="rounded-lg bg-amber-500/10 p-3 text-sm">{publicAccess
      ? t('publicScopeChanged', 'This conversation keeps its selected groups. Repository membership updates with each new message; start a new conversation to switch groups.')
      : t('scopeChanged', 'This conversation keeps its source scope. Start a new conversation to use the current selection.')}</p>}
    {!auth && !publicAccess && <p role="alert">{t('signIn', 'Sign in with GitLab to use the agent.')}</p>}
    {publicAccess && chat.session && <p className="text-xs text-[var(--muted)]">当前会话分组：{(chat.session.public_groups ?? []).map(id => publicAccess.groups.find(group => group.id === id)?.name ?? id).join('、')}</p>}
    {publicAccess && typeof chat.session?.published_repo_count === 'number' && <p role="status" className="text-xs text-[var(--muted)]">
      {t('publishedScope', 'Next new message uses the latest published group scope')}: {chat.session.published_repo_count} {t('repositories', 'repositories')}
      {chat.session.published_repo_count !== chat.session.repos.length && ` · ${t('publishedScopeChanged', 'Group membership changed; send a new message to sync. Resume keeps the original task scope.')}`}
    </p>}
    {chat.error && <div role="alert" className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-sm">
      <p className="whitespace-pre-wrap">{chat.error}</p>
      {chat.session && <button className="mt-2 underline" onClick={() => void chat.open(chat.session!.id)}>{t('reconnect', 'Reconnect')}</button>}
    </div>}
    {chat.reconnecting && <p role="status" className="text-xs text-[var(--muted)]">{t('reconnecting', 'Reconnecting; the analysis continues in the background…')}</p>}
    {chat.refresh && chat.refresh.phase !== 'ready' && <p role="status" className="text-xs text-[var(--muted)]">
      {chat.refresh.phase === 'updating' ? t('updatingCode', 'Updating code and index') : t('checkingCode', 'Checking GitLab for new code')}
      {chat.refresh.repo && ` · ${chat.refresh.repo}`}
    </p>}

    <div className="relative">
    <div ref={transcript} role="region" aria-label={t('conversation', 'Conversation')} tabIndex={0}
      className="max-h-[65dvh] min-h-48 overflow-y-auto overscroll-contain scroll-auto pr-2 [overflow-anchor:none]">
    <div ref={transcriptContent} className="flow-root">
    <div className="space-y-5 pb-4">
      {!chat.session?.runs.length && <div className="rounded-xl border border-dashed border-[var(--border-color)] p-6 text-sm text-[var(--muted)]">
        {t('empty', 'Ask where a feature is implemented, then follow up about its rules or full flow.')}
      </div>}
      {chat.session?.runs.map(run => <article key={run.id} className="space-y-3">
        {run.kind !== 'compact' && <p className="whitespace-pre-wrap rounded-xl bg-[var(--accent-primary)]/10 p-3 text-sm">{run.message}</p>}
        <RunTimeline run={run} items={chat.activities[run.id]?.items ?? []} t={t} />
        {!!run.repos?.length && <details className="text-xs text-[var(--muted)]">
          <summary className="cursor-pointer">{t('runSource', 'Source used for this answer')} · {run.repos.length} {t('repositories', 'repositories')}</summary>
          <ul className="mt-2 space-y-1">{run.repos.map(repo => <li key={repo.project}>{repo.project} <code>{repo.commit.slice(0, 12)}</code></li>)}</ul>
        </details>}
        <div className="flex items-center gap-2 text-xs text-[var(--muted)]">
          {isActive(run.status) && <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--accent-primary)]" />}
          <span>{t(run.status, run.status)}</span><span>· {run.provider} / {run.model}</span>
        </div>
        {run.error && <p className="text-sm text-red-500">{run.error}</p>}
        {run.id === latestAnalysis?.id && !running && run.status !== 'completed' &&
          <button className="btn-apple-secondary px-3 py-2 text-sm" disabled={chat.pending} onClick={() => void chat.control(run.id, 'resume', { provider: selectedProvider, model: custom ? customValue : selectedModel })}>{t('resume', 'Resume analysis')}</button>}
      </article>)}
    </div>
    <div data-chat-scroll-end aria-hidden="true" className="h-px" />
    </div>
    </div>
    {!following && <button type="button" className="btn-apple-secondary absolute bottom-3 right-4 z-10 px-3 py-1.5 text-xs shadow-sm"
      onClick={jumpToLatest}>{t('jumpLatest', '↓ Back to latest activity')}</button>}
    </div>
    {running && currentPlan && currentPlan.todos.length > 0 && <aside aria-label={t('currentPlan', 'Current plan')}
      className="rounded-xl border border-[var(--accent-primary)]/25 bg-[var(--accent-primary)]/5 p-3 text-sm">
      <h3 className="text-xs font-semibold">{t('currentPlan', 'Current plan')} · {currentPlan.todos.filter(todo => todo.status === 'completed').length}/{currentPlan.todos.length}</h3>
      <div className="max-h-32 overflow-y-auto"><PlanList todos={currentPlan.todos} t={t} /></div>
    </aside>}

    {chat.session && <aside aria-label={t('context', 'Context usage')} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[var(--border-color)] p-3 text-xs">
      <div>
        <span className="font-medium">{t('context', 'Context usage')}: </span>
        {context ? <span>{context.source === 'estimated' ? '≈' : ''}{context.input_tokens.toLocaleString()} / {context.budget_tokens.toLocaleString()} tokens
          {' · '}{Math.round(context.input_tokens / Math.max(1, context.budget_tokens) * 100)}%{' · '}
          {context.source === 'estimated' ? t('estimatedContext', 'Estimate') : t('reportedContext', 'Last request reported by provider')}</span>
          : <span>{t('contextPending', 'Available after the first model request')}</span>}
        <p className="mt-1 text-[var(--muted)]">{t('compactHint', 'Stop analysis before compacting. Compaction preserves history and does not resume analysis.')}</p>
      </div>
      <button type="button" className="btn-apple-secondary px-3 py-2" disabled={!auth || chat.pending || running || !latestAnalysis}
        onClick={() => void chat.compact(selectedProvider, custom ? customValue : selectedModel)}>{t('compact', 'Compact context')}</button>
    </aside>}

    {!!chat.session?.documents.length && <aside className="rounded-xl border border-[var(--border-color)] p-3">
      <h3 className="mb-2 text-sm font-medium">{t('documents', 'Documents')}</h3>
      <div className="flex flex-wrap gap-2">{chat.session.documents.map(item => <button key={item.name} className="btn-apple-secondary px-3 py-2 text-xs" onClick={() => setActiveDocument(item.name)}>{item.name}</button>)}</div>
      {document && <div className="mt-3 border-t border-[var(--border-color)] pt-3">
        <div className="mb-3 flex justify-between text-xs"><span>{document.name}</span><button className="underline" onClick={() => chat.download(document)}>{t('download', 'Download Markdown')}</button></div>
        <div className="max-h-[32rem] overflow-auto"><Markdown content={document.content} allowHtml={false} /></div>
      </div>}
    </aside>}

    <form onSubmit={send} className="space-y-2">
      {!running && latestAnalysis && latestAnalysis.status !== 'completed' && <p className="text-xs text-[var(--muted)]">
        {t('supplementHint', 'You can send corrections or new instructions here. Prior evidence stays in this conversation; Resume continues without changes.')}
      </p>}
      <label className="sr-only" htmlFor={`agent-question-${isGlobalAsk ? 'global' : 'repo'}`}>{t('placeholder', 'Ask about this codebase…')}</label>
      <textarea id={`agent-question-${isGlobalAsk ? 'global' : 'repo'}`} value={question} onChange={event => setQuestion(event.target.value)}
        placeholder={t('placeholder', 'Ask about this codebase…')} rows={3} maxLength={16000}
        className="w-full resize-y rounded-xl border border-[var(--border-color)] bg-[var(--background)] p-3 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--accent-primary)]/40"
        disabled={!auth || chat.pending} />
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-[var(--muted)]">{(chat.session || hasSelection) ? `${chat.session?.repos.length || (publicAccess ? publicAccess.selectedGroups.length : repos.length)} ${publicAccess && !chat.session ? '个分组' : t('repositories', 'repositories')} · ${custom ? customValue : selectedModel}` : publicAccess ? '请选择后台开放的分组。' : t('noRepos', 'Select an indexed repository first.')}</p>
        {activeRun ? <button type="button" className="btn-apple-secondary px-4 py-2 text-sm" disabled={chat.pending} onClick={() => void chat.control(activeRun.id, 'cancel')}>{t('stop', 'Stop')}</button> :
          <button type="submit" className="btn-apple px-4 py-2 text-sm disabled:opacity-40" disabled={!auth || !question.trim() || (!hasSelection && !chat.session) || chat.pending}>{t('send', 'Send')}</button>}
      </div>
    </form>
    {!publicAccess && <ModelSelectionModal isOpen={modelOpen} onClose={() => setModelOpen(false)} provider={selectedProvider} setProvider={setProvider}
      model={selectedModel} setModel={setModel} isCustomModel={custom} setIsCustomModel={setCustom} customModel={customValue}
      setCustomModel={setCustomValue} onApply={() => {}} isComprehensiveView={comprehensive} setIsComprehensiveView={setComprehensive} showWikiType={false} />}
  </section>;
}
