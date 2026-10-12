'use client';

import { useCallback, useEffect, useMemo, useState, type SetStateAction } from 'react';
import Link from 'next/link';
import RepositoryScopePicker from '@/components/RepositoryScopePicker';
import { getAuthHeaders } from '@/contexts/AuthContext';
import { agentRequestId } from '@/utils/agentClient';
import { isSearchableIndex } from '@/types/indexStatus';
import type { PublicAskGroup, PublicAskSettings as Settings } from '@/types/publicAsk';

interface Props { token: string; projects: { path: string; status: string }[] }

export default function PublicAskSettings({ token, projects }: Props) {
  const [settings, setSettings] = useState<Settings>({ enabled: false, revision: 0, groups: [] });
  const [editing, setEditing] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const group = settings.groups.find(item => item.id === editing);
  const selectedRepos = useMemo(() => new Set(group?.repos ?? []), [group?.repos]);
  const indexed = useMemo(() => projects.filter(project => isSearchableIndex(project.status)).map(project => ({
    name: project.path.split('/').at(-1) ?? project.path, path_with_namespace: project.path,
    description: '', index_status: project.status,
  })), [projects]);

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const response = await fetch('/api/admin/public-ask', { headers: getAuthHeaders(token), cache: 'no-store' });
      if (!response.ok) throw new Error('无法加载公开 Ask 设置。');
      const data: Settings = await response.json();
      setSettings(data); setEditing(data.groups[0]?.id ?? '');
    } catch (e) { setError(e instanceof Error ? e.message : '加载失败'); }
    finally { setLoading(false); }
  }, [token]);
  useEffect(() => { void load(); }, [load]);

  const updateGroup = (patch: Partial<PublicAskGroup>) => setSettings(previous => ({ ...previous,
    groups: previous.groups.map(item => item.id === editing ? { ...item, ...patch } : item),
  }));
  const setRepos = useCallback((action: SetStateAction<Set<string>>) => setSettings(previous => ({ ...previous,
    groups: previous.groups.map(item => {
      if (item.id !== editing) return item;
      const next = typeof action === 'function' ? action(new Set(item.repos)) : action;
      return { ...item, repos: Array.from(next) };
    }),
  })), [editing]);
  const addGroup = () => {
    const id = `group-${agentRequestId()}`;
    setSettings(previous => ({ ...previous, groups: [...previous.groups, { id, name: '', description: '', repos: [], enabled: true }] }));
    setEditing(id); setNotice('');
  };
  const removeGroup = () => {
    const remaining = settings.groups.filter(item => item.id !== editing);
    setSettings(previous => ({ ...previous, groups: remaining, enabled: remaining.length ? previous.enabled : false }));
    setEditing(remaining[0]?.id ?? ''); setNotice('');
  };
  const save = async () => {
    setSaving(true); setError(''); setNotice('');
    try {
      const response = await fetch('/api/admin/public-ask', { method: 'PUT', headers: { ...getAuthHeaders(token), 'Content-Type': 'application/json' }, body: JSON.stringify(settings) });
      const data = await response.json();
      if (!response.ok) {
        const detail = Array.isArray(data.detail) ? data.detail.map((item: { msg: string }) => item.msg).join('; ') : data.detail;
        throw new Error(typeof detail === 'string' ? detail : '保存失败');
      }
      setSettings(data); setNotice('已保存。访客只能选择已启用的分组，并分析其中明确勾选的仓库。');
    } catch (e) { setError(e instanceof Error ? e.message : '保存失败'); }
    finally { setSaving(false); }
  };

  return <section aria-label="公开 Ask 设置" className="glass-card space-y-4 p-5">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h2 className="font-semibold text-[var(--foreground)]">免登录 Ask</h2>
        <p className="mt-1 text-xs text-[var(--muted)]">开放的是这里勾选的仓库源码与 Wiki；不会自动开放整个 GitLab 组或新加入的仓库。</p></div>
      <Link href="/ask/public" target="_blank" rel="noopener noreferrer" className="text-sm text-[var(--accent-primary)] underline">打开访客页面 ↗</Link>
    </div>
    {error && <p role="alert" className="whitespace-pre-wrap text-sm text-red-500">{error}</p>}
    {notice && <p role="status" className="text-sm text-green-600">{notice}</p>}
    {loading ? <p role="status">加载设置…</p> : <>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={settings.enabled} onChange={event => setSettings(previous => ({ ...previous, enabled: event.target.checked }))} />启用免登录访问</label>
      <div className="flex flex-wrap gap-2">{settings.groups.map(item => <button key={item.id} type="button" onClick={() => setEditing(item.id)}
        className={`rounded-lg border px-3 py-2 text-sm ${editing === item.id ? 'border-[var(--accent-primary)] text-[var(--accent-primary)]' : 'border-[var(--border-color)]'}`}>
        {item.name || '未命名分组'} · {item.repos.length} 个仓库{!item.enabled && ' · 已关闭'}
      </button>)}<button type="button" onClick={addGroup} className="btn-apple-secondary px-3 py-2 text-sm">＋ 添加分组</button></div>
      {!group && <p className="text-sm text-[var(--muted)]">先添加 bas、easm 等逻辑分组，再为每个分组勾选仓库。</p>}
      {group && <div className="space-y-3 rounded-xl border border-[var(--border-color)] p-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-[var(--muted)]">分组名称<input aria-label="分组名称" value={group.name} maxLength={80} onChange={event => updateGroup({ name: event.target.value })}
            placeholder="例如 bas" className="mt-1 block w-full rounded-lg border border-[var(--border-color)] bg-[var(--background)] p-2 text-sm" /></label>
          <label className="text-xs text-[var(--muted)]">说明<input aria-label="分组说明" value={group.description} maxLength={500} onChange={event => updateGroup({ description: event.target.value })}
            placeholder="例如 BAS 平台业务分析" className="mt-1 block w-full rounded-lg border border-[var(--border-color)] bg-[var(--background)] p-2 text-sm" /></label>
        </div>
        <div className="flex items-center justify-between"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={group.enabled} onChange={event => updateGroup({ enabled: event.target.checked })} />开放这个分组</label>
          <button type="button" className="text-xs text-red-500 underline" onClick={removeGroup}>移除分组</button></div>
        <RepositoryScopePicker projects={indexed} selectedRepos={selectedRepos} setSelectedRepos={setRepos} loading={false} />
        {!!group.repos.length && <details className="text-xs text-[var(--muted)]"><summary>已选仓库（{group.repos.length}）</summary><ul className="mt-2 max-h-40 space-y-1 overflow-auto">
          {group.repos.map(repo => <li key={repo} className="flex items-center justify-between gap-2"><span>{repo}{!indexed.some(item => item.path_with_namespace === repo) && ' · 索引不可用'}</span>
            <button type="button" aria-label={`移除 ${repo}`} className="underline" onClick={() => updateGroup({ repos: group.repos.filter(value => value !== repo) })}>移除</button></li>)}
        </ul></details>}
      </div>}
      <div className="flex gap-2"><button type="button" onClick={() => void save()} disabled={saving} className="btn-apple px-4 py-2 text-sm">{saving ? '保存中…' : '保存公开设置'}</button>
        <button type="button" onClick={() => void load()} disabled={saving} className="btn-apple-secondary px-4 py-2 text-sm">重新加载</button></div>
    </>}
  </section>;
}
