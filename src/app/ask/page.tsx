'use client';

import React, { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import {
  FaArrowLeft,
  FaComments,
  FaWikipediaW,
} from 'react-icons/fa';
import ThemeToggle from '@/components/theme-toggle';
import Ask from '@/components/Ask';
import { useAuth, getAuthHeaders } from '@/contexts/AuthContext';
import { useLanguage } from '@/contexts/LanguageContext';
import type RepoInfo from '@/types/repoinfo';

interface IndexedProject {
  path: string;
  project_id: number | null;
  status: string;
  indexed_at: string;
}

export default function GlobalAskPage() {
  const { token, isAuthenticated, isLoading: authLoading } = useAuth();
  const { language } = useLanguage();

  const [projects, setProjects] = useState<IndexedProject[]>([]);
  const [selectedRepos, setSelectedRepos] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);

  const fetchProjects = useCallback(async () => {
    if (!token) return;
    try {
      const resp = await fetch('/api/projects', {
        headers: getAuthHeaders(token),
      });
      if (resp.ok) {
        const data = await resp.json();
        setProjects(data);
        // Default: select all indexed repos
        const allPaths = data
          .filter((p: IndexedProject) => p.status === 'indexed')
          .map((p: IndexedProject) => p.path);
        setSelectedRepos(new Set(allPaths));
      }
    } catch (err) {
      console.error('Failed to fetch projects:', err);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (isAuthenticated) {
      fetchProjects();
    }
  }, [isAuthenticated, fetchProjects]);

  const toggleRepo = (path: string) => {
    setSelectedRepos((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const selectAll = () => {
    const allPaths = projects
      .filter((p) => p.status === 'indexed')
      .map((p) => p.path);
    setSelectedRepos(new Set(allPaths));
  };

  const selectNone = () => {
    setSelectedRepos(new Set());
  };

  // Create a "global" RepoInfo placeholder
  const globalRepoInfo: RepoInfo = {
    owner: '',
    repo: '',
    type: 'gitlab',
    token: null,
    localPath: null,
    repoUrl: null,
  };

  if (authLoading) {
    return (
      <div className="h-screen flex items-center justify-center bg-[var(--background)]">
        <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-[var(--accent-primary)]"></div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <div className="h-screen flex items-center justify-center bg-[var(--background)]">
        <p className="text-[var(--muted)]">Please sign in to use Global Ask.</p>
      </div>
    );
  }

  const indexedProjects = projects.filter((p) => p.status === 'indexed');

  return (
    <div className="min-h-screen bg-[var(--background)] p-4 md:p-8">
      {/* Header */}
      <header className="max-w-4xl mx-auto mb-6">
        <div className="flex items-center justify-between glass-nav rounded-2xl p-4">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="p-2 text-[var(--muted)] hover:text-[var(--accent-primary)] transition-colors"
            >
              <FaArrowLeft />
            </Link>
            <div className="bg-[var(--accent-primary)] p-2 rounded-xl">
              <FaWikipediaW className="text-xl text-white" />
            </div>
            <div>
              <h1 className="text-xl font-semibold tracking-tight text-[var(--accent-primary)] flex items-center gap-2">
                <FaComments className="text-lg" />
                Global Ask
              </h1>
              <p className="text-xs text-[var(--muted)]">Ask across all indexed repositories</p>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="max-w-4xl mx-auto space-y-4">
        {/* Repository selector */}
        <div className="glass-card p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-[var(--foreground)]">
              Search Scope ({selectedRepos.size}/{indexedProjects.length} repos)
            </h2>
            <div className="flex items-center gap-2">
              <button
                onClick={selectAll}
                className="text-xs text-[var(--accent-primary)] hover:underline"
              >
                Select All
              </button>
              <span className="text-xs text-[var(--muted)]">|</span>
              <button
                onClick={selectNone}
                className="text-xs text-[var(--accent-primary)] hover:underline"
              >
                None
              </button>
            </div>
          </div>

          {loading ? (
            <div className="flex justify-center py-4">
              <div className="animate-spin rounded-full h-6 w-6 border-t-2 border-b-2 border-[var(--accent-primary)]"></div>
            </div>
          ) : indexedProjects.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">No indexed repositories found.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5 max-h-32 overflow-y-auto">
              {indexedProjects.map((p) => {
                const isSelected = selectedRepos.has(p.path);
                const shortName = p.path.split('/').pop() || p.path;
                return (
                  <button
                    key={p.path}
                    onClick={() => toggleRepo(p.path)}
                    className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs border transition-colors ${
                      isSelected
                        ? 'bg-[var(--accent-primary)]/10 border-[var(--accent-primary)]/30 text-[var(--accent-primary)]'
                        : 'bg-[var(--background)]/50 border-[var(--border-color)] text-[var(--muted)]'
                    }`}
                    title={p.path}
                  >
                    {shortName}
                    {isSelected && (
                      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Ask component */}
        <div className="glass-card overflow-hidden">
          <Ask
            repoInfo={globalRepoInfo}
            language={language}
            isGlobalAsk={true}
            relatedRepos={Array.from(selectedRepos)}
          />
        </div>
      </main>
    </div>
  );
}
