'use client';

import React, { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import {
  FaArrowLeft,
  FaSync,
  FaProjectDiagram,
  FaSpinner,
} from 'react-icons/fa';
import ThemeToggle from '@/components/theme-toggle';
import RelationGraph from '@/components/RelationGraph';
import { useAuth, getAuthHeaders } from '@/contexts/AuthContext';

interface RepoNode {
  path: string;
  summary: string;
  tech_stack: string[];
  related: string[];
}

interface Edge {
  from: string;
  to: string;
  type: string;
  description: string;
}

interface RelationsData {
  analyzed_at: string | null;
  repos: Record<string, RepoNode>;
  edges: Edge[];
  mermaid: string;
}

interface AnalysisStatus {
  running: boolean;
  progress: string;
  error: string | null;
}

export default function RelationsPage() {
  const { token, isAuthenticated, isAdmin, isLoading: authLoading } = useAuth();

  const [data, setData] = useState<RelationsData | null>(null);
  const [status, setStatus] = useState<AnalysisStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);

  const fetchRelations = useCallback(async () => {
    if (!token) return;
    try {
      const resp = await fetch('/api/admin/repo-relations', {
        headers: getAuthHeaders(token),
      });
      if (resp.ok) {
        setData(await resp.json());
      }
    } catch (err) {
      console.error('Failed to fetch relations:', err);
    } finally {
      setLoading(false);
    }
  }, [token]);

  const fetchStatus = useCallback(async () => {
    if (!token) return;
    try {
      const resp = await fetch('/api/admin/repo-relations/status', {
        headers: getAuthHeaders(token),
      });
      if (resp.ok) {
        const s = await resp.json();
        setStatus(s);
        setAnalyzing(s.running);
      }
    } catch (err) {
      console.error('Failed to fetch analysis status:', err);
    }
  }, [token]);

  useEffect(() => {
    if (isAuthenticated && isAdmin) {
      fetchRelations();
      fetchStatus();
    }
  }, [isAuthenticated, isAdmin, fetchRelations, fetchStatus]);

  // Poll status while analyzing
  useEffect(() => {
    if (!analyzing) return;
    const interval = setInterval(async () => {
      await fetchStatus();
      if (!analyzing) {
        await fetchRelations();
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [analyzing, fetchStatus, fetchRelations]);

  // Refetch after analysis completes
  useEffect(() => {
    if (status && !status.running && analyzing) {
      setAnalyzing(false);
      fetchRelations();
    }
  }, [status, analyzing, fetchRelations]);

  const handleAnalyze = async () => {
    if (!token) return;
    setAnalyzing(true);
    try {
      await fetch('/api/admin/repo-relations/analyze', {
        method: 'POST',
        headers: {
          ...getAuthHeaders(token),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({}),
      });
      // Start polling
      fetchStatus();
    } catch (err) {
      console.error('Failed to trigger analysis:', err);
      setAnalyzing(false);
    }
  };

  if (authLoading) {
    return (
      <div className="h-screen flex items-center justify-center bg-[var(--background)]">
        <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-[var(--accent-primary)]"></div>
      </div>
    );
  }

  if (!isAuthenticated || !isAdmin) {
    return (
      <div className="h-screen flex items-center justify-center bg-[var(--background)]">
        <p className="text-[var(--muted)]">Admin access required</p>
      </div>
    );
  }

  const edgeTypeBadge = (type: string) => {
    const colors: Record<string, string> = {
      depends_on: 'bg-[#0071e3]/10 text-[#0071e3] border-[#0071e3]/20',
      likely_depends_on: 'bg-[#ff9f0a]/10 text-[#ff9f0a] border-[#ff9f0a]/20',
      provides_api_for: 'bg-[#30d158]/10 text-[#30d158] border-[#30d158]/20',
      shares_protocol: 'bg-[#bf5af2]/10 text-[#bf5af2] border-[#bf5af2]/20',
    };
    const cls = colors[type] || 'bg-[var(--muted)]/10 text-[var(--muted)] border-[var(--muted)]/20';
    return (
      <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium border ${cls}`}>
        {type.replace(/_/g, ' ')}
      </span>
    );
  };

  return (
    <div className="min-h-screen bg-[var(--background)] p-4 md:p-8">
      {/* Header — glass nav */}
      <header className="max-w-7xl mx-auto mb-6 sticky top-4 z-20">
        <div className="glass-nav flex items-center justify-between rounded-2xl p-4">
          <div className="flex items-center gap-3">
            <Link
              href="/admin"
              className="p-2 text-[var(--muted)] hover:text-[var(--accent-primary)] transition-colors"
            >
              <FaArrowLeft />
            </Link>
            <FaProjectDiagram className="text-xl text-[var(--accent-primary)]" />
            <h1 className="text-xl font-semibold tracking-tight text-[var(--foreground)]">
              Repository Relations
            </h1>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleAnalyze}
              disabled={analyzing}
              className="btn-apple flex items-center gap-2"
            >
              {analyzing ? (
                <FaSpinner className="animate-spin" />
              ) : (
                <FaSync className="text-xs" />
              )}
              {analyzing ? 'Analyzing...' : 'Analyze Relations'}
            </button>
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto">
        {/* Progress bar */}
        {analyzing && status && (
          <div className="mb-6">
            <div className="w-full bg-[var(--border-color)] rounded-full h-1.5 mb-2">
              <div className="bg-[var(--accent-primary)] h-1.5 rounded-full animate-pulse" style={{ width: '60%' }} />
            </div>
            <p className="text-xs text-[var(--muted)]">
              {status.progress || 'Running analysis...'}
            </p>
          </div>
        )}

        {loading ? (
          <div className="flex justify-center py-12">
            <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-[var(--accent-primary)]"></div>
          </div>
        ) : !data || !data.analyzed_at ? (
          /* Empty state */
          <div className="glass-card p-16 text-center">
            <FaProjectDiagram className="text-5xl text-[var(--muted)] mx-auto mb-4" />
            <h2 className="text-xl font-semibold tracking-tight text-[var(--foreground)] mb-2">
              No Relations Data
            </h2>
            <p className="text-[var(--muted)] text-sm mb-8 max-w-md mx-auto">
              Click &quot;Analyze Relations&quot; to scan indexed repositories and discover relationships.
            </p>
            <button onClick={handleAnalyze} disabled={analyzing} className="btn-apple">
              Start Analysis
            </button>
          </div>
        ) : (
          <>
            {/* Stats cards — Apple big number style */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
              <div className="glass-card p-5">
                <p className="text-xs text-[var(--muted)] uppercase tracking-wider font-medium">Repositories</p>
                <p className="text-4xl font-light tracking-tight text-[var(--foreground)] mt-1">
                  {Object.keys(data.repos).length}
                </p>
              </div>
              <div className="glass-card p-5">
                <p className="text-xs text-[var(--muted)] uppercase tracking-wider font-medium">Relationships</p>
                <p className="text-4xl font-light tracking-tight text-[var(--foreground)] mt-1">
                  {data.edges.length}
                </p>
              </div>
              <div className="glass-card p-5">
                <p className="text-xs text-[var(--muted)] uppercase tracking-wider font-medium">Last Analyzed</p>
                <p className="text-sm text-[var(--foreground)] mt-2">
                  {new Date(data.analyzed_at).toLocaleString()}
                </p>
              </div>
            </div>

            {/* Interactive Relation Graph */}
            <div className="glass-card p-6 mb-6">
              <h2 className="text-lg font-semibold tracking-tight text-[var(--foreground)] mb-4">
                Dependency Graph
              </h2>
              <RelationGraph data={data} />
            </div>

            {/* Edge list — card style */}
            <div className="glass-card p-6">
              <h2 className="text-lg font-semibold tracking-tight text-[var(--foreground)] mb-4">
                Relationships ({data.edges.length})
              </h2>
              {data.edges.length === 0 ? (
                <p className="text-[var(--muted)] text-sm">No relationships discovered.</p>
              ) : (
                <div className="space-y-2">
                  {data.edges.map((edge, i) => (
                    <div
                      key={i}
                      className="flex items-center gap-4 p-3 rounded-xl bg-[var(--background)]/50 hover:bg-[var(--accent-primary)]/5 transition-colors"
                    >
                      <span className="text-sm font-medium text-[var(--foreground)] min-w-[100px] truncate">
                        {edge.from.split('/').pop()}
                      </span>
                      <span className="shrink-0">{edgeTypeBadge(edge.type)}</span>
                      <svg className="w-4 h-4 text-[var(--muted)] shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7l5 5m0 0l-5 5m5-5H6" />
                      </svg>
                      <span className="text-sm font-medium text-[var(--foreground)] min-w-[100px] truncate">
                        {edge.to.split('/').pop()}
                      </span>
                      <span className="text-xs text-[var(--muted)] flex-1 truncate ml-2">
                        {edge.description}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
