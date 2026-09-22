'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import { useLanguage } from '@/contexts/LanguageContext';
import { useAgentConversation } from '@/hooks/useAgentConversation';
import getRepoUrl from '@/utils/getRepoUrl';
import { agentRequest } from '@/utils/agentClient';
import Markdown from '@/components/Markdown';
import ModelSelectionModal from '@/components/ModelSelectionModal';
import { isActive, type AskProps } from './types';

const normalize = (repo: string) => {
  const path = repo.includes('://') ? new URL(repo).pathname : repo;
  return decodeURIComponent(path).replace(/^\/+|\/+$/g, '').replace(/\.git$/, '');
};

export default function AgentChat({ repoInfo, provider = '', model = '', isCustomModel = false,
  customModel = '', language = 'en', onRef, relatedRepos = [], isGlobalAsk = false }: AskProps) {
  const { token } = useAuth();
  const { messages } = useLanguage();
  const t = (key: string, fallback: string): string => messages.agent?.[key] || fallback;
  const chat = useAgentConversation(token);
  const [question, setQuestion] = useState('');
  const [selectedProvider, setProvider] = useState(provider);
  const [selectedModel, setModel] = useState(model);
  const [custom, setCustom] = useState(isCustomModel);
  const [customValue, setCustomValue] = useState(customModel);
  const [modelOpen, setModelOpen] = useState(false);
  const [comprehensive, setComprehensive] = useState(true);
  const [activeDocument, setActiveDocument] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const primary = isGlobalAsk ? '' : getRepoUrl(repoInfo);
  const scopeKey = JSON.stringify([...new Set([primary, ...relatedRepos].filter(Boolean))].sort());
  const repos: string[] = useMemo(() => JSON.parse(scopeKey), [scopeKey]);
  const lastRun = chat.session?.runs.at(-1);
  const running = isActive(lastRun?.status);
  const unfinished = !!lastRun && lastRun.status !== 'completed';
  const scopeChanged = !!chat.session && JSON.stringify(chat.session.repos.map(repo => repo.project).sort()) !==
    JSON.stringify([...new Set(repos.map(normalize))].sort());
  const document = chat.session?.documents.find(item => item.name === activeDocument);

  useEffect(() => { onRef?.({ clearConversation: chat.reset }); }, [onRef, chat.reset]);
  useEffect(() => {
    setProvider(provider); setModel(model); setCustom(isCustomModel); setCustomValue(customModel);
  }, [provider, model, isCustomModel, customModel]);
  useEffect(() => {
    if ((provider && model) || !token) return;
    let live = true;
    agentRequest<{ defaultProvider: string; defaultModel: string }>(token, '/config').then(data => {
      if (!live) return;
      setProvider(data.defaultProvider || '');
      setModel(data.defaultModel || '');
    }).catch(() => {});
    return () => { live = false; };
  }, [provider, model, token]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'nearest' }); }, [lastRun?.answer]);

  const newConversation = () => { chat.reset(); setQuestion(''); setActiveDocument(''); };
  const send = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!question.trim() || running || unfinished || (!repos.length && !chat.session)) return;
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
        <button className="btn-apple-secondary px-3 py-2" onClick={() => setModelOpen(true)} disabled={running}>{t('model', 'Model')}</button>
      </div>
    </header>

    {historyOpen && <nav aria-label={t('history', 'History')} className="max-h-52 overflow-y-auto rounded-xl border border-[var(--border-color)] p-2 space-y-1">
      {chat.sessions.length === 0 && <p className="p-2 text-sm text-[var(--muted)]">{t('noHistory', 'No conversations yet.')}</p>}
      {chat.sessions.map(item => <button key={item.id} onClick={() => { void chat.open(item.id); setActiveDocument(''); }}
        className={`block w-full rounded-lg p-2 text-left text-sm hover:bg-[var(--background)] ${chat.session?.id === item.id ? 'text-[var(--accent-primary)]' : ''}`}>
        <span className="block truncate">{item.title || t('newChat', 'New conversation')}</span>
        <span className="text-xs text-[var(--muted)]">{item.repos.length} {t('repositories', 'repositories')} · {new Date(item.updated_at).toLocaleString()}</span>
      </button>)}
      {chat.nextOffset !== null && <button className="p-2 text-sm" onClick={() => void chat.refreshHistory(chat.nextOffset ?? 0)}>{t('loadMore', 'Load more')}</button>}
    </nav>}

    {chat.session && <details className="text-xs text-[var(--muted)]">
      <summary className="cursor-pointer">{t('scope', 'Source snapshot')} · {chat.session.repos.length} {t('repositories', 'repositories')}</summary>
      <ul className="mt-2 space-y-1">{chat.session.repos.map(repo => <li key={repo.project}>{repo.project} <code>{repo.commit.slice(0, 12)}</code></li>)}</ul>
    </details>}
    {scopeChanged && <p role="status" className="rounded-lg bg-amber-500/10 p-3 text-sm">{t('scopeChanged', 'This conversation keeps its source scope. Start a new conversation to use the current selection.')}</p>}
    {!token && <p role="alert">{t('signIn', 'Sign in with GitLab to use the agent.')}</p>}
    {chat.error && <div role="alert" className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-sm">
      <p>{chat.error}</p>
      {chat.session && <button className="mt-2 underline" onClick={() => void chat.open(chat.session!.id)}>{t('reconnect', 'Reconnect')}</button>}
    </div>}
    {chat.reconnecting && <p role="status" className="text-xs text-[var(--muted)]">{t('reconnecting', 'Reconnecting; the analysis continues in the background…')}</p>}

    <div className="space-y-5" aria-live="polite">
      {!chat.session?.runs.length && <div className="rounded-xl border border-dashed border-[var(--border-color)] p-6 text-sm text-[var(--muted)]">
        {t('empty', 'Ask where a feature is implemented, then follow up about its rules or full flow.')}
      </div>}
      {chat.session?.runs.map(run => <article key={run.id} className="space-y-3">
        <p className="whitespace-pre-wrap rounded-xl bg-[var(--accent-primary)]/10 p-3 text-sm">{run.message}</p>
        <div className="px-1"><Markdown content={run.answer} allowHtml={false} /></div>
        <div className="flex items-center gap-2 text-xs text-[var(--muted)]">
          {isActive(run.status) && <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--accent-primary)]" />}
          <span>{t(run.status, run.status)}</span><span>· {run.provider} / {run.model}</span>
        </div>
        {run.error && <p className="text-sm text-red-500">{run.error}</p>}
        {run.id === lastRun?.id && !isActive(run.status) && run.status !== 'completed' &&
          <button className="btn-apple-secondary px-3 py-2 text-sm" disabled={chat.pending} onClick={() => void chat.control(run.id, 'resume', { provider: selectedProvider, model: custom ? customValue : selectedModel })}>{t('resume', 'Resume analysis')}</button>}
      </article>)}
      <div ref={bottom} />
    </div>

    {(chat.tools.length > 0 || chat.todos.length > 0) && <details open={running} className="rounded-xl border border-[var(--border-color)] p-3 text-xs">
      <summary className="cursor-pointer font-medium">{t('progress', 'Analysis activity')} ({chat.tools.length})</summary>
      {chat.todos.length > 0 && <ul className="mt-3 space-y-1">{chat.todos.map((todo, i) => <li key={i}>{todo.status === 'completed' ? '✓' : '○'} {todo.content}</li>)}</ul>}
      <ul className="mt-3 max-h-40 overflow-y-auto space-y-1">{chat.tools.map(tool => <li key={tool.id} className="text-[var(--muted)]">
        {tool.error ? '!' : tool.done ? '✓' : '…'} {tool.name}
      </li>)}</ul>
    </details>}

    {!!chat.session?.documents.length && <aside className="rounded-xl border border-[var(--border-color)] p-3">
      <h3 className="mb-2 text-sm font-medium">{t('documents', 'Documents')}</h3>
      <div className="flex flex-wrap gap-2">{chat.session.documents.map(item => <button key={item.name} className="btn-apple-secondary px-3 py-2 text-xs" onClick={() => setActiveDocument(item.name)}>{item.name}</button>)}</div>
      {document && <div className="mt-3 border-t border-[var(--border-color)] pt-3">
        <div className="mb-3 flex justify-between text-xs"><span>{document.name}</span><button className="underline" onClick={() => chat.download(document)}>{t('download', 'Download Markdown')}</button></div>
        <div className="max-h-[32rem] overflow-auto"><Markdown content={document.content} allowHtml={false} /></div>
      </div>}
    </aside>}

    <form onSubmit={send} className="space-y-2">
      <label className="sr-only" htmlFor={`agent-question-${isGlobalAsk ? 'global' : 'repo'}`}>{t('placeholder', 'Ask about this codebase…')}</label>
      <textarea id={`agent-question-${isGlobalAsk ? 'global' : 'repo'}`} value={question} onChange={event => setQuestion(event.target.value)}
        placeholder={t('placeholder', 'Ask about this codebase…')} rows={3} maxLength={16000}
        className="w-full resize-y rounded-xl border border-[var(--border-color)] bg-[var(--background)] p-3 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--accent-primary)]/40"
        disabled={!token || chat.pending} />
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-[var(--muted)]">{(chat.session?.repos.length || repos.length) ? `${chat.session?.repos.length || repos.length} ${t('repositories', 'repositories')} · ${custom ? customValue : selectedModel}` : t('noRepos', 'Select an indexed repository first.')}</p>
        {running && lastRun ? <button type="button" className="btn-apple-secondary px-4 py-2 text-sm" disabled={chat.pending} onClick={() => void chat.control(lastRun.id, 'cancel')}>{t('stop', 'Stop')}</button> :
          <button type="submit" className="btn-apple px-4 py-2 text-sm disabled:opacity-40" disabled={!token || !question.trim() || (!repos.length && !chat.session) || chat.pending || unfinished}>{t('send', 'Send')}</button>}
      </div>
    </form>
    <ModelSelectionModal isOpen={modelOpen} onClose={() => setModelOpen(false)} provider={selectedProvider} setProvider={setProvider}
      model={selectedModel} setModel={setModel} isCustomModel={custom} setIsCustomModel={setCustom} customModel={customValue}
      setCustomModel={setCustomValue} onApply={() => {}} isComprehensiveView={comprehensive} setIsComprehensiveView={setComprehensive} showWikiType={false} />
  </section>;
}
