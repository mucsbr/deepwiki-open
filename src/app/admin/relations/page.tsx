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
import Mermaid from '@/components/Mermaid';
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

  return (
    <div className="min-h-screen paper-texture p-4 md:p-8">
      {/* Header */}
      <header className="max-w-7xl mx-auto mb-6">
        <div className="flex items-center justify-between bg-[var(--card-bg)] rounded-lg shadow-custom border border-[var(--border-color)] p-4">
          <div className="flex items-center gap-3">
            <Link
              href="/admin"
              className="p-2 text-[var(--muted)] hover:text-[var(--accent-primary)] transition-colors"
            >
              <FaArrowLeft />
            </Link>
            <FaProjectDiagram className="text-xl text-[var(--accent-primary)]" />
            <h1 className="text-xl font-bold text-[var(--foreground)]">
              Repository Relations
            </h1>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleAnalyze}
              disabled={analyzing}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                analyzing
                  ? 'bg-gray-300 dark:bg-gray-600 text-gray-500 dark:text-gray-400 cursor-not-allowed'
                  : 'bg-[var(--accent-primary)] text-white hover:bg-[var(--accent-primary)]/90'
              }`}
            >
              {analyzing ? (
                <FaSpinner className="animate-spin" />
              ) : (
                <FaSync />
              )}
              {analyzing ? 'Analyzing...' : 'Analyze Relations'}
            </button>
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto">
        {/* Status banner */}
        {analyzing && status && (
          <div className="mb-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
            <div className="flex items-center gap-2">
              <FaSpinner className="animate-spin text-blue-500" />
              <span className="text-blue-700 dark:text-blue-300 text-sm">
                {status.progress || 'Running analysis...'}
              </span>
            </div>
          </div>
        )}

        {loading ? (
          <div className="flex justify-center py-12">
            <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-[var(--accent-primary)]"></div>
          </div>
        ) : !data || !data.analyzed_at ? (
          <div className="bg-[var(--card-bg)] rounded-lg shadow-custom border border-[var(--border-color)] p-12 text-center">
            <FaProjectDiagram className="text-4xl text-[var(--muted)] mx-auto mb-4" />
            <h2 className="text-lg font-medium text-[var(--foreground)] mb-2">
              No Relations Data
            </h2>
            <p className="text-[var(--muted)] text-sm mb-6">
              Click &quot;Analyze Relations&quot; to scan indexed repositories and discover relationships.
            </p>
          </div>
        ) : (
          <>
            {/* Stats */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
              <div className="bg-[var(--card-bg)] rounded-lg shadow-custom border border-[var(--border-color)] p-4">
                <p className="text-xs text-[var(--muted)] uppercase tracking-wide">Repositories</p>
                <p className="text-2xl font-bold text-[var(--foreground)]">
                  {Object.keys(data.repos).length}
                </p>
              </div>
              <div className="bg-[var(--card-bg)] rounded-lg shadow-custom border border-[var(--border-color)] p-4">
                <p className="text-xs text-[var(--muted)] uppercase tracking-wide">Relationships</p>
                <p className="text-2xl font-bold text-[var(--foreground)]">
                  {data.edges.length}
                </p>
              </div>
              <div className="bg-[var(--card-bg)] rounded-lg shadow-custom border border-[var(--border-color)] p-4">
                <p className="text-xs text-[var(--muted)] uppercase tracking-wide">Last Analyzed</p>
                <p className="text-sm text-[var(--foreground)]">
                  {new Date(data.analyzed_at).toLocaleString()}
                </p>
              </div>
            </div>

            {/* Mermaid diagram */}
            <div className="bg-[var(--card-bg)] rounded-lg shadow-custom border border-[var(--border-color)] p-6 mb-6">
              <h2 className="text-lg font-bold text-[var(--foreground)] mb-4">
                Dependency Graph
              </h2>
              {data.mermaid ? (
                <Mermaid chart={data.mermaid} />
              ) : (
                <p className="text-[var(--muted)] text-sm">No graph data available.</p>
              )}
            </div>

            {/* Edge list */}
            <div className="bg-[var(--card-bg)] rounded-lg shadow-custom border border-[var(--border-color)] p-6">
              <h2 className="text-lg font-bold text-[var(--foreground)] mb-4">
                Relationships ({data.edges.length})
              </h2>
              {data.edges.length === 0 ? (
                <p className="text-[var(--muted)] text-sm">No relationships discovered.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-[var(--border-color)]">
                        <th className="text-left py-2 px-3 text-[var(--muted)] font-medium">From</th>
                        <th className="text-left py-2 px-3 text-[var(--muted)] font-medium">Type</th>
                        <th className="text-left py-2 px-3 text-[var(--muted)] font-medium">To</th>
                        <th className="text-left py-2 px-3 text-[var(--muted)] font-medium">Description</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.edges.map((edge, i) => (
                        <tr key={i} className="border-b border-[var(--border-color)]/50 hover:bg-[var(--background)]/50">
                          <td className="py-2 px-3 text-[var(--foreground)]">
                            {edge.from.split('/').pop()}
                          </td>
                          <td className="py-2 px-3">
                            <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                              edge.type === 'depends_on'
                                ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
                                : edge.type === 'provides_api_for'
                                ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                                : edge.type === 'shares_protocol'
                                ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400'
                                : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300'
                            }`}>
                              {edge.type.replace(/_/g, ' ')}
                            </span>
                          </td>
                          <td className="py-2 px-3 text-[var(--foreground)]">
                            {edge.to.split('/').pop()}
                          </td>
                          <td className="py-2 px-3 text-[var(--muted)] text-xs">
                            {edge.description}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
