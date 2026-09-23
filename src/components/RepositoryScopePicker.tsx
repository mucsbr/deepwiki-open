'use client';

import { useMemo, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react';
import { FaChevronDown, FaChevronRight, FaFolder, FaSearch } from 'react-icons/fa';
import { useLanguage } from '@/contexts/LanguageContext';

export interface ScopeProject {
  name: string;
  path_with_namespace: string;
  description: string | null;
}

interface ScopeGroup {
  name: string;
  path: string;
  children: Map<string, ScopeGroup>;
  projects: ScopeProject[];
  paths: string[];
}

function makeGroup(name = '', path = ''): ScopeGroup {
  return { name, path, children: new Map(), projects: [], paths: [] };
}

function buildTree(projects: ScopeProject[]): ScopeGroup {
  const root = makeGroup();
  for (const project of projects) {
    const parts = project.path_with_namespace.split('/');
    let group = root;
    group.paths.push(project.path_with_namespace);
    for (const name of parts.slice(0, -1)) {
      let child = group.children.get(name);
      if (!child) {
        child = makeGroup(name, group.path ? `${group.path}/${name}` : name);
        group.children.set(name, child);
      }
      group = child;
      group.paths.push(project.path_with_namespace);
    }
    group.projects.push(project);
  }
  return root;
}

interface Props {
  projects: ScopeProject[];
  selectedRepos: Set<string>;
  setSelectedRepos: Dispatch<SetStateAction<Set<string>>>;
  loading: boolean;
}

export default function RepositoryScopePicker({ projects, selectedRepos, setSelectedRepos, loading }: Props) {
  const { messages } = useLanguage();
  const t = (key: string, fallback: string): string => messages.ask?.[key] || fallback;
  const [query, setQuery] = useState('');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [searchCollapsed, setSearchCollapsed] = useState<Set<string>>(new Set());
  const search = query.trim().toLocaleLowerCase();
  const matches = useMemo(() => projects.filter(project => !search ||
    project.path_with_namespace.toLocaleLowerCase().includes(search) ||
    project.name.toLocaleLowerCase().includes(search) ||
    project.description?.toLocaleLowerCase().includes(search)), [projects, search]);
  const tree = useMemo(() => buildTree(matches), [matches]);

  const setPaths = (paths: string[]) => {
    setSelectedRepos(previous => {
      const next = new Set(previous);
      const allSelected = paths.every(path => next.has(path));
      for (const path of paths) {
        if (allSelected) next.delete(path);
        else next.add(path);
      }
      return next;
    });
  };

  const toggleExpanded = (path: string) => {
    const setter = search ? setSearchCollapsed : setExpanded;
    setter(previous => {
      const next = new Set(previous);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const renderProject = (project: ScopeProject) => <label key={project.path_with_namespace}
    title={project.path_with_namespace}
    className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-[var(--accent-primary)]/5">
    <input type="checkbox" checked={selectedRepos.has(project.path_with_namespace)}
      onChange={() => setPaths([project.path_with_namespace])}
      aria-label={project.path_with_namespace} className="h-4 w-4 shrink-0 accent-[var(--accent-primary)]" />
    <span className="min-w-0 truncate text-[var(--foreground)]">{project.path_with_namespace.split('/').pop()}</span>
  </label>;

  const renderGroup = (group: ScopeGroup, depth = 0): ReactNode => {
    const count = group.paths.filter(path => selectedRepos.has(path)).length;
    const open = search ? !searchCollapsed.has(group.path) : expanded.has(group.path);
    const children = [...group.children.values()].sort((a, b) => a.name.localeCompare(b.name));
    const projectsInGroup = [...group.projects].sort((a, b) => a.name.localeCompare(b.name));
    return <div key={group.path} className={depth === 0 ? 'border-b border-[var(--border-color)]/50 last:border-b-0' : ''}>
      <div className="flex min-w-0 items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-[var(--accent-primary)]/5">
        <input type="checkbox" checked={count === group.paths.length}
          ref={element => { if (element) element.indeterminate = count > 0 && count < group.paths.length; }}
          onChange={() => setPaths(group.paths)}
          aria-label={`${t('scopeGroup', 'Select group')} ${group.path}`}
          className="h-4 w-4 shrink-0 accent-[var(--accent-primary)]" />
        <button type="button" onClick={() => toggleExpanded(group.path)} aria-expanded={open}
          aria-label={`${open ? t('scopeCollapse', 'Collapse') : t('scopeExpand', 'Expand')} ${group.path}`}
          className="flex min-w-0 flex-1 items-center gap-2 text-left text-sm">
          {open ? <FaChevronDown className="shrink-0 text-xs text-[var(--muted)]" /> : <FaChevronRight className="shrink-0 text-xs text-[var(--muted)]" />}
          <FaFolder className="shrink-0 text-[var(--accent-primary)]" />
          <span className="truncate text-[var(--foreground)]" title={group.path}>{group.name}</span>
          <span className="ml-auto shrink-0 text-xs text-[var(--muted)]">{count}/{group.paths.length}</span>
        </button>
      </div>
      {open && <div className="ml-5 border-l border-[var(--border-color)] pl-2">
        {children.map(child => renderGroup(child, depth + 1))}
        {projectsInGroup.map(renderProject)}
      </div>}
    </div>;
  };

  return <div className="glass-card p-4">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-sm font-medium text-[var(--foreground)]">
        {t('scopeTitle', 'Search Scope')} ({selectedRepos.size}/{projects.length})
      </h2>
      <div className="flex items-center gap-3 text-xs">
        <button type="button" onClick={() => setSelectedRepos(previous => {
          const next = new Set(previous);
          for (const project of matches) next.add(project.path_with_namespace);
          return next;
        })}
          disabled={matches.length === 0 || matches.every(project => selectedRepos.has(project.path_with_namespace))}
          className="text-[var(--accent-primary)] hover:underline disabled:opacity-40">
          {search ? t('scopeSelectMatches', 'Select matches') : t('scopeSelectAll', 'Select all')}
        </button>
        <button type="button" onClick={() => setSelectedRepos(previous => {
          const next = new Set(previous);
          for (const project of matches) next.delete(project.path_with_namespace);
          return next;
        })} disabled={!matches.some(project => selectedRepos.has(project.path_with_namespace))}
          className="text-[var(--accent-primary)] hover:underline disabled:opacity-40">
          {search ? t('scopeClearMatches', 'Clear matches') : t('scopeClearAll', 'Clear all')}
        </button>
      </div>
    </div>
    <div className="relative mb-2">
      <FaSearch aria-hidden="true" className="absolute left-3 top-1/2 -translate-y-1/2 text-xs text-[var(--muted)]" />
      <input type="search" value={query} onChange={event => { setQuery(event.target.value); setSearchCollapsed(new Set()); }}
        placeholder={t('scopeSearch', 'Search repository or group path…')}
        aria-label={t('scopeSearch', 'Search repository or group path…')}
        className="input-apple w-full rounded-lg py-2 pl-9 pr-3 text-sm" />
    </div>
    {loading ? <div className="flex justify-center py-4"><div className="h-6 w-6 animate-spin rounded-full border-2 border-t-transparent border-[var(--accent-primary)]" /></div>
      : projects.length === 0 ? <p className="text-sm text-[var(--muted)]">{t('scopeEmpty', 'No indexed repositories found.')}</p>
      : matches.length === 0 ? <p className="py-4 text-center text-sm text-[var(--muted)]">{t('scopeNoMatches', 'No repositories match your search.')}</p>
      : <div className="max-h-80 overflow-y-auto rounded-lg border border-[var(--border-color)] p-1">
        {[...tree.children.values()].sort((a, b) => a.name.localeCompare(b.name)).map(group => renderGroup(group))}
        {[...tree.projects].sort((a, b) => a.name.localeCompare(b.name)).map(renderProject)}
      </div>}
    {search && matches.length > 0 && <p className="mt-2 text-xs text-[var(--muted)]">
      {t('scopeMatchingCount', '{count} matching repositories').replace('{count}', String(matches.length))}
    </p>}
  </div>;
}
