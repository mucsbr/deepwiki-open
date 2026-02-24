'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import {
  FaWikipediaW,
  FaArrowLeft,
  FaSync,
  FaDatabase,
  FaExclamationTriangle,
  FaCheckCircle,
  FaServer,
  FaPlay,
  FaSearch,
  FaFilter,
  FaChevronRight,
  FaChevronDown,
  FaRedo,
  FaBookOpen,
  FaProjectDiagram,
} from 'react-icons/fa';
import ThemeToggle from '@/components/theme-toggle';
import { useAuth, getAuthHeaders } from '@/contexts/AuthContext';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Stats {
  total_indexed_projects: number;
  status_counts: Record<string, number>;
  total_wiki_caches: number;
  disk_usage: { repos_mb: number; databases_mb: number; wikicache_mb: number };
  last_batch_run: string | null;
}

interface Project {
  path: string;
  project_id: number | null;
  status: string;
  indexed_at: string;
  last_activity_at: string;
  repo_path: string;
}

interface BatchStatus {
  running: boolean;
  operation?: string; // "reindex" | "regenerate_wiki" | "batch_index"
  progress: {
    current?: number;
    total?: number;
    current_project?: string;
    status?: string;
  };
  last_result: Record<string, unknown>;
  last_run: string | null;
}

interface SystemConfig {
  gitlab_url: string;
  embedder_type: string;
  batch_groups: string;
  permission_cache_ttl: number;
  admin_usernames: string[];
}

interface GitLabGroup {
  id: number;
  name: string;
  full_path: string;
  description: string;
}

interface GroupProject {
  id: number;
  name: string;
  path_with_namespace: string;
  last_activity_at: string;
  is_indexed: boolean;
  index_status: string | null;
}

interface UpdateInfo {
  stored: string;
  current: string | null;
  needs_update: boolean;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function AdminPage() {
  const router = useRouter();
  const { token, isAdmin, isLoading: authLoading } = useAuth();

  // Data states
  const [stats, setStats] = useState<Stats | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [batchStatus, setBatchStatus] = useState<BatchStatus | null>(null);
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [loading, setLoading] = useState(true);

  // Tab state
  const [activeTab, setActiveTab] = useState<'indexed' | 'batch'>('indexed');

  // Update detection
  const [updateInfo, setUpdateInfo] = useState<Record<string, UpdateInfo>>({});
  const [updateCheckLoading, setUpdateCheckLoading] = useState(false);

  // Single project operation state
  const [operatingProject, setOperatingProject] = useState<string | null>(null);

  // Project list filters
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');

  // Batch index selection states
  const [groups, setGroups] = useState<GitLabGroup[]>([]);
  const [expandedGroups, setExpandedGroups] = useState<Set<number>>(new Set());
  const [groupProjects, setGroupProjects] = useState<Record<number, GroupProject[]>>({});
  const [loadingGroups, setLoadingGroups] = useState<Set<number>>(new Set());
  const [selectedGroups, setSelectedGroups] = useState<Set<number>>(new Set());
  const [selectedProjects, setSelectedProjects] = useState<Set<number>>(new Set());
  const [forceReindex, setForceReindex] = useState(false);

  // Project search states
  const [projectSearch, setProjectSearch] = useState('');
  const [searchResults, setSearchResults] = useState<GroupProject[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);

  // ---------------------------------------------------------------------------
  // Data fetching
  // ---------------------------------------------------------------------------

  const headers = useMemo(
    () => ({ ...getAuthHeaders(token), 'Content-Type': 'application/json' }),
    [token]
  );

  const fetchAll = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const [statsRes, projectsRes, configRes, batchRes, groupsRes] = await Promise.all([
        fetch('/api/admin/stats', { headers }),
        fetch('/api/admin/projects', { headers }),
        fetch('/api/admin/config', { headers }),
        fetch('/api/admin/batch-index/status', { headers }),
        fetch('/api/admin/groups', { headers }),
      ]);

      if (statsRes.ok) setStats(await statsRes.json());
      if (projectsRes.ok) setProjects(await projectsRes.json());
      if (configRes.ok) setConfig(await configRes.json());
      if (batchRes.ok) setBatchStatus(await batchRes.json());
      if (groupsRes.ok) setGroups(await groupsRes.json());
    } catch (err) {
      console.error('Failed to fetch admin data:', err);
    } finally {
      setLoading(false);
    }
  }, [token, headers]);

  const checkUpdates = useCallback(async () => {
    if (!token) return;
    setUpdateCheckLoading(true);
    try {
      const res = await fetch('/api/admin/check-updates', { headers });
      if (res.ok) {
        setUpdateInfo(await res.json());
      }
    } catch (err) {
      console.error('Failed to check updates:', err);
    } finally {
      setUpdateCheckLoading(false);
    }
  }, [token, headers]);

  const triggerSingleOperation = useCallback(async (
    projectPath: string,
    operation: 'reindex' | 'regenerate-wiki',
  ) => {
    setOperatingProject(projectPath);
    try {
      const res = await fetch(`/api/admin/projects/${projectPath}/${operation}`, {
        method: 'POST',
        headers,
      });
      if (res.ok) {
        setBatchStatus((prev) =>
          prev ? { ...prev, running: true, operation: operation === 'reindex' ? 'reindex' : 'regenerate_wiki', progress: { status: 'starting', current_project: projectPath } } : prev
        );
      } else {
        const err = await res.json();
        alert(err.detail || `Failed to start ${operation}`);
      }
    } catch (err) {
      console.error(`Single ${operation} error:`, err);
    } finally {
      setOperatingProject(null);
    }
  }, [headers]);

  useEffect(() => {
    if (authLoading) return;
    if (!isAdmin) {
      router.replace('/');
      return;
    }
    fetchAll().then(() => checkUpdates());
  }, [authLoading, isAdmin, fetchAll, checkUpdates, router]);

  // Batch status polling
  useEffect(() => {
    if (!batchStatus?.running) return;
    const interval = setInterval(async () => {
      try {
        const res = await fetch('/api/admin/batch-index/status', { headers });
        if (res.ok) {
          const data: BatchStatus = await res.json();
          setBatchStatus(data);
          if (!data.running) {
            // Refresh stats when done
            const statsRes = await fetch('/api/admin/stats', { headers });
            if (statsRes.ok) setStats(await statsRes.json());
          }
        }
      } catch {
        /* ignore polling errors */
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [batchStatus?.running, headers]);

  // ---------------------------------------------------------------------------
  // Group / project selection logic
  // ---------------------------------------------------------------------------

  const handleProjectSearch = useCallback(async () => {
    if (!projectSearch.trim()) {
      setSearchResults([]);
      return;
    }
    setSearchLoading(true);
    try {
      const res = await fetch(
        `/api/admin/projects/search?q=${encodeURIComponent(projectSearch.trim())}`,
        { headers }
      );
      if (res.ok) {
        setSearchResults(await res.json());
      }
    } catch (err) {
      console.error('Project search error:', err);
    } finally {
      setSearchLoading(false);
    }
  }, [projectSearch, headers]);

  const toggleGroupExpand = async (groupId: number) => {
    const next = new Set(expandedGroups);
    if (next.has(groupId)) {
      next.delete(groupId);
    } else {
      next.add(groupId);
      // Fetch projects for this group if not already loaded
      if (!groupProjects[groupId]) {
        setLoadingGroups((prev) => new Set(prev).add(groupId));
        try {
          const res = await fetch(`/api/admin/groups/${groupId}/projects`, { headers });
          if (res.ok) {
            const data: GroupProject[] = await res.json();
            setGroupProjects((prev) => ({ ...prev, [groupId]: data }));
          }
        } catch (err) {
          console.error(`Failed to fetch projects for group ${groupId}:`, err);
        } finally {
          setLoadingGroups((prev) => {
            const s = new Set(prev);
            s.delete(groupId);
            return s;
          });
        }
      }
    }
    setExpandedGroups(next);
  };

  const toggleGroupSelect = (groupId: number) => {
    const next = new Set(selectedGroups);
    if (next.has(groupId)) {
      next.delete(groupId);
      // Also deselect all projects in this group
      const gProjects = groupProjects[groupId] || [];
      const nextProjects = new Set(selectedProjects);
      gProjects.forEach((p) => nextProjects.delete(p.id));
      setSelectedProjects(nextProjects);
    } else {
      next.add(groupId);
      // Also select all projects in this group (if loaded)
      const gProjects = groupProjects[groupId] || [];
      const nextProjects = new Set(selectedProjects);
      gProjects.forEach((p) => nextProjects.add(p.id));
      setSelectedProjects(nextProjects);
    }
    setSelectedGroups(next);
  };

  const toggleProjectSelect = (projectId: number, groupId: number) => {
    const nextProjects = new Set(selectedProjects);
    if (nextProjects.has(projectId)) {
      nextProjects.delete(projectId);
      // If group was selected, deselect it (partial selection)
      const nextGroups = new Set(selectedGroups);
      nextGroups.delete(groupId);
      setSelectedGroups(nextGroups);
    } else {
      nextProjects.add(projectId);
      // Check if all projects in this group are now selected
      const gProjects = groupProjects[groupId] || [];
      const allSelected = gProjects.every((p) => nextProjects.has(p.id));
      if (allSelected && gProjects.length > 0) {
        setSelectedGroups((prev) => new Set(prev).add(groupId));
      }
    }
    setSelectedProjects(nextProjects);
  };

  // Count total selected items
  const selectedCount = useMemo(() => {
    // For fully selected groups, count their projects
    // For individually selected projects not in a selected group, count those too
    const projectsInSelectedGroups = new Set<number>();
    selectedGroups.forEach((gid) => {
      (groupProjects[gid] || []).forEach((p) => projectsInSelectedGroups.add(p.id));
    });
    // Add individually selected projects not already covered
    const allSelected = new Set([...projectsInSelectedGroups, ...selectedProjects]);
    return allSelected.size;
  }, [selectedGroups, selectedProjects, groupProjects]);

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  const triggerOperation = async (operation: 'batch_index' | 'reindex' | 'regenerate_wiki') => {
    // Build the request body from selection
    const groupIdsToIndex = Array.from(selectedGroups);
    // Individual projects: those selected but NOT part of a fully selected group
    const projectsInSelectedGroups = new Set<number>();
    selectedGroups.forEach((gid) => {
      (groupProjects[gid] || []).forEach((p) => projectsInSelectedGroups.add(p.id));
    });
    const individualProjectIds = Array.from(selectedProjects).filter(
      (pid) => !projectsInSelectedGroups.has(pid)
    );

    const body: { group_ids?: number[]; project_ids?: number[]; force?: boolean } = {};
    if (groupIdsToIndex.length > 0) body.group_ids = groupIdsToIndex;
    if (individualProjectIds.length > 0) body.project_ids = individualProjectIds;
    if (forceReindex) body.force = true;

    const endpointMap: Record<string, string> = {
      batch_index: '/api/admin/batch-index',
      reindex: '/api/admin/reindex',
      regenerate_wiki: '/api/admin/regenerate-wiki',
    };

    try {
      const res = await fetch(endpointMap[operation], {
        method: 'POST',
        headers,
        body: JSON.stringify(Object.keys(body).length > 0 ? body : null),
      });
      if (res.ok) {
        setBatchStatus((prev) =>
          prev ? { ...prev, running: true, operation, progress: { status: 'starting' } } : prev
        );
      } else {
        const err = await res.json();
        alert(err.detail || 'Failed to start operation');
      }
    } catch (err) {
      console.error('Trigger operation error:', err);
    }
  };

  // ---------------------------------------------------------------------------
  // Filtered projects (Area 2)
  // ---------------------------------------------------------------------------

  const filteredProjects = useMemo(() => {
    let list = projects;
    if (statusFilter !== 'all') {
      list = list.filter((p) => p.status === statusFilter);
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      list = list.filter((p) => p.path.toLowerCase().includes(q));
    }
    return list;
  }, [projects, statusFilter, searchQuery]);

  const availableStatuses = useMemo(() => {
    const s = new Set(projects.map((p) => p.status));
    return Array.from(s).sort();
  }, [projects]);

  const needsUpdateCount = useMemo(() => {
    return Object.values(updateInfo).filter((u) => u.needs_update).length;
  }, [updateInfo]);

  // ---------------------------------------------------------------------------
  // Loading / unauthorized guards
  // ---------------------------------------------------------------------------

  if (authLoading || loading) {
    return (
      <div className="h-screen flex items-center justify-center bg-[var(--background)]">
        <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-[var(--accent-primary)]" />
      </div>
    );
  }

  if (!isAdmin) return null;

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const totalDisk = stats
    ? (stats.disk_usage.repos_mb + stats.disk_usage.databases_mb + stats.disk_usage.wikicache_mb).toFixed(1)
    : '0';

  return (
    <div className="min-h-screen paper-texture p-4 md:p-8 flex flex-col">
      {/* Header */}
      <header className="max-w-7xl mx-auto mb-6 w-full">
        <div className="flex items-center justify-between bg-[var(--card-bg)] rounded-lg shadow-custom border border-[var(--border-color)] p-4">
          <div className="flex items-center gap-3">
            <Link href="/" className="p-2 text-[var(--muted)] hover:text-[var(--accent-primary)] transition-colors">
              <FaArrowLeft />
            </Link>
            <div className="bg-[var(--accent-primary)] p-2 rounded-lg">
              <FaWikipediaW className="text-xl text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-[var(--accent-primary)]">Admin Dashboard</h1>
              <p className="text-xs text-[var(--muted)]">System Management</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Link
              href="/admin/relations"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border border-[var(--border-color)] text-[var(--foreground)] hover:bg-[var(--accent-primary)]/5 transition-colors"
              title="View Repository Relations"
            >
              <FaProjectDiagram className="text-xs" />
              Relations
            </Link>
            <button
              onClick={fetchAll}
              className="p-2 text-[var(--muted)] hover:text-[var(--accent-primary)] transition-colors"
              title="Refresh"
            >
              <FaSync />
            </button>
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="flex-1 max-w-7xl mx-auto w-full space-y-6">
        {/* ================================================================ */}
        {/* Area 1: Stats Cards */}
        {/* ================================================================ */}
        <section className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            icon={<FaDatabase className="text-blue-500" />}
            label="Indexed Projects"
            value={stats?.total_indexed_projects ?? 0}
          />
          <StatCard
            icon={<FaExclamationTriangle className="text-red-500" />}
            label="Index Errors"
            value={stats?.status_counts?.error ?? 0}
            highlight={!!stats?.status_counts?.error}
          />
          <StatCard
            icon={<FaCheckCircle className="text-green-500" />}
            label="Wiki Caches"
            value={stats?.total_wiki_caches ?? 0}
          />
          <StatCard
            icon={<FaServer className="text-purple-500" />}
            label="Disk Usage"
            value={`${totalDisk} MB`}
          />
        </section>

        {/* ================================================================ */}
        {/* Tab Navigation */}
        {/* ================================================================ */}
        <section className="bg-[var(--card-bg)] rounded-lg shadow-custom border border-[var(--border-color)]">
          {/* Tab bar */}
          <div className="flex border-b border-[var(--border-color)]">
            <button
              onClick={() => setActiveTab('indexed')}
              className={`px-6 py-3 text-sm font-medium transition-colors relative ${
                activeTab === 'indexed'
                  ? 'text-[var(--accent-primary)]'
                  : 'text-[var(--muted)] hover:text-[var(--foreground)]'
              }`}
            >
              Indexed Projects
              {needsUpdateCount > 0 && (
                <span className="ml-2 px-1.5 py-0.5 rounded-full text-xs bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400">
                  {needsUpdateCount} needs update
                </span>
              )}
              {activeTab === 'indexed' && (
                <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-[var(--accent-primary)]" />
              )}
            </button>
            <button
              onClick={() => setActiveTab('batch')}
              className={`px-6 py-3 text-sm font-medium transition-colors relative ${
                activeTab === 'batch'
                  ? 'text-[var(--accent-primary)]'
                  : 'text-[var(--muted)] hover:text-[var(--foreground)]'
              }`}
            >
              Batch Indexing
              {activeTab === 'batch' && (
                <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-[var(--accent-primary)]" />
              )}
            </button>
          </div>

          {/* Tab content */}
          <div className="p-6">
            {/* ============================================================ */}
            {/* Tab 1: Indexed Projects */}
            {/* ============================================================ */}
            {activeTab === 'indexed' && (
              <>
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
                  <h2 className="text-lg font-bold text-[var(--foreground)]">
                    Indexed Projects ({filteredProjects.length})
                  </h2>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={checkUpdates}
                      disabled={updateCheckLoading}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border border-[var(--border-color)] text-[var(--foreground)] hover:bg-[var(--accent-primary)]/5 transition-colors disabled:opacity-50"
                      title="Check for updates from GitLab"
                    >
                      <FaSync className={`text-xs ${updateCheckLoading ? 'animate-spin' : ''}`} />
                      Check Updates
                    </button>
                    <div className="relative">
                      <FaSearch className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--muted)] text-xs" />
                      <input
                        type="text"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        placeholder="Search..."
                        className="pl-8 pr-3 py-1.5 text-sm border border-[var(--border-color)] rounded-md bg-transparent text-[var(--foreground)] focus:outline-none focus:border-[var(--accent-primary)]"
                      />
                    </div>
                    <div className="relative flex items-center">
                      <FaFilter className="absolute left-2.5 text-[var(--muted)] text-xs pointer-events-none" />
                      <select
                        value={statusFilter}
                        onChange={(e) => setStatusFilter(e.target.value)}
                        className="pl-7 pr-3 py-1.5 text-sm border border-[var(--border-color)] rounded-md bg-transparent text-[var(--foreground)]"
                      >
                        <option value="all">All</option>
                        {availableStatuses.map((s) => (
                          <option key={s} value={s}>{s}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-[var(--border-color)] text-left text-[var(--muted)]">
                        <th className="pb-2 pr-4">Project Path</th>
                        <th className="pb-2 pr-4">Status</th>
                        <th className="pb-2 pr-4">Indexed At</th>
                        <th className="pb-2 pr-4">Last Activity</th>
                        <th className="pb-2 text-right">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredProjects.length === 0 ? (
                        <tr>
                          <td colSpan={5} className="py-8 text-center text-[var(--muted)]">
                            No projects found.
                          </td>
                        </tr>
                      ) : (
                        filteredProjects.map((p) => {
                          const update = updateInfo[p.path];
                          const needsUpdate = update?.needs_update ?? false;
                          return (
                            <tr
                              key={p.path}
                              className={`border-b border-[var(--border-color)]/50 ${
                                needsUpdate
                                  ? 'bg-yellow-50/50 dark:bg-yellow-900/10'
                                  : 'hover:bg-[var(--accent-primary)]/5'
                              }`}
                            >
                              <td className="py-2 pr-4 font-medium text-[var(--foreground)]">
                                <div className="flex items-center gap-2">
                                  {p.path}
                                  {needsUpdate && (
                                    <span className="px-1.5 py-0.5 rounded text-xs bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400 whitespace-nowrap">
                                      needs update
                                    </span>
                                  )}
                                </div>
                              </td>
                              <td className="py-2 pr-4"><StatusBadge status={p.status} /></td>
                              <td className="py-2 pr-4 text-[var(--muted)]">
                                {p.indexed_at ? new Date(p.indexed_at).toLocaleString() : '-'}
                              </td>
                              <td className="py-2 pr-4 text-[var(--muted)]">
                                {p.last_activity_at ? new Date(p.last_activity_at).toLocaleString() : '-'}
                              </td>
                              <td className="py-2 text-right">
                                <div className="flex items-center justify-end gap-1.5">
                                  <button
                                    onClick={() => triggerSingleOperation(p.path, 'reindex')}
                                    disabled={batchStatus?.running || operatingProject === p.path}
                                    className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded border border-blue-300 text-blue-600 hover:bg-blue-50 dark:border-blue-700 dark:text-blue-400 dark:hover:bg-blue-900/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                                    title="Reindex this project (git pull + re-embedding)"
                                  >
                                    <FaRedo className="text-[10px]" />
                                    Reindex
                                  </button>
                                  <button
                                    onClick={() => triggerSingleOperation(p.path, 'regenerate-wiki')}
                                    disabled={batchStatus?.running || operatingProject === p.path}
                                    className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded border border-green-300 text-green-600 hover:bg-green-50 dark:border-green-700 dark:text-green-400 dark:hover:bg-green-900/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                                    title="Regenerate wiki cache for this project"
                                  >
                                    <FaBookOpen className="text-[10px]" />
                                    Regen Wiki
                                  </button>
                                </div>
                              </td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>
              </>
            )}

            {/* ============================================================ */}
            {/* Tab 2: Batch Indexing */}
            {/* ============================================================ */}
            {activeTab === 'batch' && (
              <>
                <h2 className="text-lg font-bold text-[var(--foreground)] mb-4">Batch Indexing</h2>

                {/* Project search */}
                <div className="mb-4">
                  <div className="flex items-center gap-2 mb-2">
                    <div className="relative flex-1">
                      <FaSearch className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--muted)] text-xs" />
                      <input
                        type="text"
                        value={projectSearch}
                        onChange={(e) => setProjectSearch(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleProjectSearch()}
                        placeholder="Search projects by name (press Enter)..."
                        className="w-full pl-8 pr-3 py-1.5 text-sm border border-[var(--border-color)] rounded-md bg-transparent text-[var(--foreground)] focus:outline-none focus:border-[var(--accent-primary)]"
                      />
                    </div>
                    <button
                      onClick={handleProjectSearch}
                      disabled={searchLoading || !projectSearch.trim()}
                      className="px-3 py-1.5 text-sm rounded-md bg-[var(--accent-primary)] text-white hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {searchLoading ? 'Searching...' : 'Search'}
                    </button>
                  </div>
                  {searchResults.length > 0 && (
                    <div className="border border-[var(--border-color)] rounded-md p-2 mb-2 max-h-48 overflow-y-auto">
                      <p className="text-xs text-[var(--muted)] mb-1">
                        Found {searchResults.length} projects — check to add to index selection
                      </p>
                      {searchResults.map((p) => (
                        <div
                          key={p.id}
                          className="flex items-center gap-2 py-1 px-2 rounded hover:bg-[var(--accent-primary)]/5"
                        >
                          <label className="flex items-center gap-2 flex-1 cursor-pointer select-none">
                            <input
                              type="checkbox"
                              checked={selectedProjects.has(p.id)}
                              onChange={() => {
                                const next = new Set(selectedProjects);
                                if (next.has(p.id)) next.delete(p.id);
                                else next.add(p.id);
                                setSelectedProjects(next);
                              }}
                              className="accent-[var(--accent-primary)]"
                            />
                            <span className="text-sm text-[var(--foreground)]">{p.path_with_namespace}</span>
                          </label>
                          <IndexStatusBadge status={p.index_status} />
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Group list */}
                {groups.length === 0 ? (
                  <p className="text-sm text-[var(--muted)]">No groups configured (set GITLAB_BATCH_GROUPS).</p>
                ) : (
                  <div className="space-y-1 mb-4">
                    {groups.map((group) => {
                      const isExpanded = expandedGroups.has(group.id);
                      const isGroupSelected = selectedGroups.has(group.id);
                      const gProjects = groupProjects[group.id] || [];
                      const isLoadingGroup = loadingGroups.has(group.id);

                      const selectedInGroup = gProjects.filter((p) => selectedProjects.has(p.id)).length;
                      const isIndeterminate = !isGroupSelected && selectedInGroup > 0;

                      return (
                        <div key={group.id}>
                          <div className="flex items-center gap-2 p-2 rounded-md hover:bg-[var(--accent-primary)]/5">
                            <button
                              onClick={() => toggleGroupExpand(group.id)}
                              className="p-1 text-[var(--muted)] hover:text-[var(--accent-primary)] transition-colors"
                            >
                              {isExpanded ? <FaChevronDown className="text-xs" /> : <FaChevronRight className="text-xs" />}
                            </button>
                            <label className="flex items-center gap-2 flex-1 cursor-pointer select-none">
                              <input
                                type="checkbox"
                                checked={isGroupSelected}
                                ref={(el) => {
                                  if (el) el.indeterminate = isIndeterminate;
                                }}
                                onChange={() => toggleGroupSelect(group.id)}
                                className="accent-[var(--accent-primary)]"
                              />
                              <span className="font-medium text-sm text-[var(--foreground)]">{group.name}</span>
                              <span className="text-xs text-[var(--muted)]">({group.full_path})</span>
                              {gProjects.length > 0 && (
                                <span className="text-xs text-[var(--muted)]">
                                  &middot; {gProjects.length} projects
                                </span>
                              )}
                            </label>
                          </div>

                          {isExpanded && (
                            <div className="ml-10 border-l border-[var(--border-color)] pl-3 pb-1">
                              {isLoadingGroup ? (
                                <p className="text-xs text-[var(--muted)] py-2">Loading projects...</p>
                              ) : gProjects.length === 0 ? (
                                <p className="text-xs text-[var(--muted)] py-2">No projects in this group.</p>
                              ) : (
                                gProjects.map((p) => (
                                  <div
                                    key={p.id}
                                    className="flex items-center gap-2 py-1 px-2 rounded hover:bg-[var(--accent-primary)]/5"
                                  >
                                    <label className="flex items-center gap-2 flex-1 cursor-pointer select-none">
                                      <input
                                        type="checkbox"
                                        checked={selectedProjects.has(p.id)}
                                        onChange={() => toggleProjectSelect(p.id, group.id)}
                                        className="accent-[var(--accent-primary)]"
                                      />
                                      <span className="text-sm text-[var(--foreground)]">{p.path_with_namespace}</span>
                                    </label>
                                    <IndexStatusBadge status={p.index_status} />
                                  </div>
                                ))
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* Action bar */}
                <div className="flex flex-col sm:flex-row sm:items-center gap-4 pt-3 border-t border-[var(--border-color)]">
                  <div className="flex items-center gap-2 flex-wrap">
                    <button
                      onClick={() => triggerOperation('batch_index')}
                      disabled={batchStatus?.running || selectedCount === 0}
                      className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-[var(--accent-primary)] text-white font-medium hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
                      title="Full pipeline: git pull + embedding + wiki generation"
                    >
                      <FaPlay className="text-xs" />
                      {batchStatus?.running && batchStatus.operation === 'batch_index'
                        ? 'Running...'
                        : selectedCount > 0
                          ? `Full Index (${selectedCount})`
                          : 'Select projects'}
                    </button>

                    <button
                      onClick={() => triggerOperation('reindex')}
                      disabled={batchStatus?.running || selectedCount === 0}
                      className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white font-medium hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
                      title="Only git pull + re-embedding, no wiki generation"
                    >
                      <FaDatabase className="text-xs" />
                      {batchStatus?.running && batchStatus.operation === 'reindex'
                        ? 'Running...'
                        : 'Reindex Only'}
                    </button>

                    <button
                      onClick={() => triggerOperation('regenerate_wiki')}
                      disabled={batchStatus?.running || selectedCount === 0}
                      className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-green-600 text-white font-medium hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
                      title="Only regenerate wiki cache using existing embeddings"
                    >
                      <FaWikipediaW className="text-xs" />
                      {batchStatus?.running && batchStatus.operation === 'regenerate_wiki'
                        ? 'Running...'
                        : 'Regen Wiki Only'}
                    </button>
                  </div>

                  <label className="flex items-center gap-2 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={forceReindex}
                      onChange={(e) => setForceReindex(e.target.checked)}
                      className="accent-[var(--accent-primary)]"
                    />
                    <span className="text-sm text-[var(--foreground)]">Force Re-index</span>
                    <span className="text-xs text-[var(--muted)]">(ignore cache, re-index all selected)</span>
                  </label>

                  {stats?.last_batch_run && (
                    <span className="text-sm text-[var(--muted)]">
                      Last run: {new Date(stats.last_batch_run).toLocaleString()}
                    </span>
                  )}
                </div>
              </>
            )}

            {/* Progress (shared across both tabs) */}
            {batchStatus?.running && (
              <div className="mt-4 space-y-2">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-[var(--foreground)]">
                    <span className="font-medium text-[var(--muted)] mr-2">
                      {batchStatus.operation === 'reindex' ? '[Reindex]' :
                       batchStatus.operation === 'regenerate_wiki' ? '[Wiki Regen]' :
                       '[Full Index]'}
                    </span>
                    {batchStatus.progress.current_project || 'Processing...'}
                  </span>
                  {batchStatus.progress.total && (
                    <span className="text-[var(--muted)]">
                      {batchStatus.progress.current}/{batchStatus.progress.total}
                    </span>
                  )}
                </div>
                {batchStatus.progress.total && (
                  <div className="w-full bg-[var(--border-color)] rounded-full h-2.5">
                    <div
                      className="bg-[var(--accent-primary)] h-2.5 rounded-full transition-all"
                      style={{
                        width: `${Math.round(((batchStatus.progress.current ?? 0) / batchStatus.progress.total) * 100)}%`,
                      }}
                    />
                  </div>
                )}
              </div>
            )}

            {/* Last result (shared across both tabs) */}
            {!batchStatus?.running && batchStatus?.last_result && Object.keys(batchStatus.last_result).length > 0 && (
              <div className="mt-4 p-3 rounded-md bg-[var(--background)] border border-[var(--border-color)] text-sm">
                <h3 className="font-medium text-[var(--foreground)] mb-2">Last Result</h3>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[var(--muted)]">
                  {Object.entries(batchStatus.last_result).map(([k, v]) => (
                    <div key={k}>
                      <span className="font-medium">{k}:</span> {String(v)}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>

        {/* ================================================================ */}
        {/* Area 4: System Configuration */}
        {/* ================================================================ */}
        <section className="bg-[var(--card-bg)] rounded-lg shadow-custom border border-[var(--border-color)] p-6">
          <h2 className="text-lg font-bold text-[var(--foreground)] mb-4">System Configuration</h2>
          {config ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
              <ConfigItem label="GitLab URL" value={config.gitlab_url} />
              <ConfigItem label="Embedder Type" value={config.embedder_type} />
              <ConfigItem label="Batch Groups" value={config.batch_groups} />
              <ConfigItem label="Permission Cache TTL" value={`${config.permission_cache_ttl}s`} />
              <ConfigItem label="Admin Users" value={config.admin_usernames.join(', ') || '(none)'} />
            </div>
          ) : (
            <p className="text-[var(--muted)] text-sm">Loading configuration...</p>
          )}
        </section>
      </main>

      <footer className="max-w-7xl mx-auto mt-8 w-full">
        <div className="flex justify-center bg-[var(--card-bg)] rounded-lg p-4 border border-[var(--border-color)] shadow-custom">
          <p className="text-[var(--muted)] text-sm">DeepWiki Admin Dashboard</p>
        </div>
      </footer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatCard({
  icon,
  label,
  value,
  highlight,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | string;
  highlight?: boolean;
}) {
  return (
    <div className="bg-[var(--card-bg)] rounded-lg shadow-custom border border-[var(--border-color)] p-4 flex items-center gap-3">
      <div className="text-2xl">{icon}</div>
      <div>
        <p className="text-xs text-[var(--muted)]">{label}</p>
        <p className={`text-xl font-bold ${highlight ? 'text-red-500' : 'text-[var(--foreground)]'}`}>
          {value}
        </p>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    indexed: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
    error: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  };
  const cls = colors[status] || 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-400';
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${cls}`}>{status}</span>;
}

function IndexStatusBadge({ status }: { status: string | null }) {
  if (!status) {
    return <span className="px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">new</span>;
  }
  return <StatusBadge status={status} />;
}

function ConfigItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="p-3 rounded-md bg-[var(--background)] border border-[var(--border-color)]">
      <p className="text-xs text-[var(--muted)] mb-1">{label}</p>
      <p className="text-[var(--foreground)] font-medium break-all">{value}</p>
    </div>
  );
}
