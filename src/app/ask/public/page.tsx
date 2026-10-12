'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import ThemeToggle from '@/components/theme-toggle';
import AgentChat from '@/components/agent/AgentChat';
import { publicVisitorKey } from '@/utils/agentClient';
import { useLanguage } from '@/contexts/LanguageContext';
import type { PublicAskCatalog } from '@/types/publicAsk';

export default function PublicAskPage() {
  const { language } = useLanguage();
  const [catalog, setCatalog] = useState<PublicAskCatalog | null>(null);
  const [visitorKey, setVisitorKey] = useState<string | null>(null);
  const [selectedGroups, setSelectedGroups] = useState<string[]>([]);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setError('');
    try {
      const response = await fetch('/api/public-agent/config', { cache: 'no-store' });
      if (!response.ok) throw new Error('暂时无法加载公开 Ask 配置，请稍后重试。');
      const data: PublicAskCatalog = await response.json();
      setCatalog(data);
      setSelectedGroups(previous => previous.filter(id => data.groups.some(group => group.id === id)));
    } catch (e) { setError(e instanceof Error ? e.message : '加载失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { setVisitorKey(publicVisitorKey()); void reload(); }, [reload]);
  const sessionSettings = useCallback((groups: string[], prompt: string) => {
    setSelectedGroups(groups); setSystemPrompt(prompt);
  }, []);
  const publicAccess = useMemo(() => ({ visitorKey, groups: catalog?.groups ?? [], selectedGroups,
    systemPrompt, onSessionSettings: sessionSettings }), [visitorKey, catalog?.groups, selectedGroups, systemPrompt, sessionSettings]);

  return <div className="min-h-screen bg-[var(--background)] p-4 md:p-8">
    <header className="glass-nav mx-auto mb-6 flex max-w-4xl items-center justify-between rounded-2xl p-4">
      <div><h1 className="text-xl font-semibold text-[var(--accent-primary)]">公开 Ask</h1>
        <p className="mt-1 text-xs text-[var(--muted)]">无需登录 · 仅分析后台开放的仓库 · 历史按浏览器分别保存</p></div>
      <ThemeToggle />
    </header>
    <main className="mx-auto max-w-4xl space-y-4">
      {loading && <p role="status">正在加载分组…</p>}
      {error && <div role="alert" className="glass-card space-y-2 p-4"><p>{error}</p><button onClick={() => void reload()} className="btn-apple-secondary px-3 py-2">重新加载</button></div>}
      {!loading && catalog && !catalog.enabled && <p role="status" className="glass-card p-6">管理员尚未开放此 Ask 页面。</p>}
      {catalog?.enabled && <>
        <section aria-label="开放分组" className="glass-card space-y-3 p-4">
          <div className="flex items-center justify-between"><h2 className="font-medium">选择分组</h2><button onClick={() => void reload()} className="text-xs underline">刷新分组</button></div>
          <p className="text-xs text-[var(--muted)]">可多选。分组选择用于新会话，历史会话保留原来的仓库范围。</p>
          <div className="grid gap-3 sm:grid-cols-2">{catalog.groups.map(group => <label key={group.id}
            className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3 ${selectedGroups.includes(group.id) ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/5' : 'border-[var(--border-color)]'}`}>
            <input type="checkbox" className="mt-1" checked={selectedGroups.includes(group.id)}
              onChange={event => setSelectedGroups(previous => event.target.checked ? [...previous, group.id] : previous.filter(id => id !== group.id))} />
            <span><span className="block font-medium">{group.name}</span><span className="block text-xs text-[var(--muted)]">{group.repo_count} 个仓库{group.description && ` · ${group.description}`}</span></span>
          </label>)}</div>
          <p className="text-xs text-[var(--muted)]">同时处理最多 {catalog.maxConcurrentRuns ?? 4} 个分析任务，其余任务排队。</p>
        </section>
        <details className="glass-card p-4">
          <summary className="cursor-pointer font-medium">自定义系统提示词</summary>
          <label htmlFor="public-system-prompt" className="mt-3 block text-xs text-[var(--muted)]">留空沿用现有默认提示词；修改会在下一次提问时生效，恢复任务仍使用该任务原来的提示词。</label>
          <textarea id="public-system-prompt" aria-label="自定义系统提示词" value={systemPrompt} onChange={event => setSystemPrompt(event.target.value)} rows={6} maxLength={16000}
            placeholder="例如：你是一名业务流程分析师。先定位入口，使用中文解释主路径，并给出代码出处和尚未确认的环节。"
            className="mt-2 w-full rounded-xl border border-[var(--border-color)] bg-[var(--background)] p-3 text-sm" />
          <button type="button" onClick={() => setSystemPrompt('')} className="mt-2 text-xs underline">恢复默认提示词</button>
        </details>
        <div className="glass-card overflow-hidden"><AgentChat repoInfo={{ owner: '', repo: '', type: 'gitlab', token: null, localPath: null, repoUrl: null }}
          isGlobalAsk language={language} provider={catalog.defaultProvider} model={catalog.defaultModel} publicAccess={publicAccess} /></div>
      </>}
    </main>
  </div>;
}
