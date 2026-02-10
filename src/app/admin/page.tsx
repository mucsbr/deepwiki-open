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

  // Project list filters
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');

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
      const [statsRes, projectsRes, configRes, batchRes] = await Promise.all([
        fetch('/api/admin/stats', { headers }),
        fetch('/api/admin/projects', { headers }),
        fetch('/api/admin/config', { headers }),
        fetch('/api/admin/batch-index/status', { headers }),
      ]);

      if (statsRes.ok) setStats(await statsRes.json());
      if (projectsRes.ok) setProjects(await projectsRes.json());
      if (configRes.ok) setConfig(await configRes.json());
      if (batchRes.ok) setBatchStatus(await batchRes.json());
    } catch (err) {
      console.error('Failed to fetch admin data:', err);
    } finally {
      setLoading(false);
    }
  }, [token, headers]);

  useEffect(() => {
    if (authLoading) return;
    if (!isAdmin) {
      router.replace('/');
      return;
    }
    fetchAll();
  }, [authLoading, isAdmin, fetchAll, router]);

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
  // Actions
  // ---------------------------------------------------------------------------

  const triggerBatchIndex = async () => {
    try {
      const res = await fetch('/api/admin/batch-index', {
        method: 'POST',
        headers,
      });
      if (res.ok) {
        setBatchStatus((prev) => (prev ? { ...prev, running: true, progress: { status: 'starting' } } : prev));
      } else {
        const err = await res.json();
        alert(err.detail || 'Failed to start batch index');
      }
    } catch (err) {
      console.error('Trigger batch index error:', err);
    }
  };

  // ---------------------------------------------------------------------------
  // Filtered projects
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

  // Available statuses for filter
  const availableStatuses = useMemo(() => {
    const s = new Set(projects.map((p) => p.status));
    return Array.from(s).sort();
  }, [projects]);

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
        {/* Area 2: Project List */}
        {/* ================================================================ */}
        <section className="bg-[var(--card-bg)] rounded-lg shadow-custom border border-[var(--border-color)] p-6">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
            <h2 className="text-lg font-bold text-[var(--foreground)]">
              Indexed Projects ({filteredProjects.length})
            </h2>
            <div className="flex items-center gap-2">
              {/* Search */}
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
              {/* Status filter */}
              <div className="relative flex items-center">
                <FaFilter className="absolute left-2.5 text-[var(--muted)] text-xs pointer-events-none" />
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  className="pl-7 pr-3 py-1.5 text-sm border border-[var(--border-color)] rounded-md bg-transparent text-[var(--foreground)]"
                >
                  <option value="all">All</option>
                  {availableStatuses.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {/* Table */}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--border-color)] text-left text-[var(--muted)]">
                  <th className="pb-2 pr-4">Project Path</th>
                  <th className="pb-2 pr-4">Status</th>
                  <th className="pb-2 pr-4">Indexed At</th>
                  <th className="pb-2">Last Activity</th>
                </tr>
              </thead>
              <tbody>
                {filteredProjects.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="py-8 text-center text-[var(--muted)]">
                      No projects found.
                    </td>
                  </tr>
                ) : (
                  filteredProjects.map((p) => (
                    <tr key={p.path} className="border-b border-[var(--border-color)]/50 hover:bg-[var(--accent-primary)]/5">
                      <td className="py-2 pr-4 font-medium text-[var(--foreground)]">{p.path}</td>
                      <td className="py-2 pr-4">
                        <StatusBadge status={p.status} />
                      </td>
                      <td className="py-2 pr-4 text-[var(--muted)]">
                        {p.indexed_at ? new Date(p.indexed_at).toLocaleString() : '-'}
                      </td>
                      <td className="py-2 text-[var(--muted)]">
                        {p.last_activity_at ? new Date(p.last_activity_at).toLocaleString() : '-'}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>

        {/* ================================================================ */}
        {/* Area 3: Batch Index */}
        {/* ================================================================ */}
        <section className="bg-[var(--card-bg)] rounded-lg shadow-custom border border-[var(--border-color)] p-6">
          <h2 className="text-lg font-bold text-[var(--foreground)] mb-4">Batch Indexing</h2>

          <div className="flex flex-col sm:flex-row sm:items-center gap-4">
            <button
              onClick={triggerBatchIndex}
              disabled={batchStatus?.running}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-[var(--accent-primary)] text-white font-medium hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <FaPlay className="text-xs" />
              {batchStatus?.running ? 'Running...' : 'Trigger Batch Index'}
            </button>

            {stats?.last_batch_run && (
              <span className="text-sm text-[var(--muted)]">
                Last run: {new Date(stats.last_batch_run).toLocaleString()}
              </span>
            )}
          </div>

          {/* Progress */}
          {batchStatus?.running && batchStatus.progress?.total && (
            <div className="mt-4 space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-[var(--foreground)]">
                  {batchStatus.progress.current_project || 'Processing...'}
                </span>
                <span className="text-[var(--muted)]">
                  {batchStatus.progress.current}/{batchStatus.progress.total}
                </span>
              </div>
              <div className="w-full bg-[var(--border-color)] rounded-full h-2.5">
                <div
                  className="bg-[var(--accent-primary)] h-2.5 rounded-full transition-all"
                  style={{
                    width: `${Math.round(((batchStatus.progress.current ?? 0) / batchStatus.progress.total) * 100)}%`,
                  }}
                />
              </div>
            </div>
          )}

          {/* Last result */}
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

function ConfigItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="p-3 rounded-md bg-[var(--background)] border border-[var(--border-color)]">
      <p className="text-xs text-[var(--muted)] mb-1">{label}</p>
      <p className="text-[var(--foreground)] font-medium break-all">{value}</p>
    </div>
  );
}
