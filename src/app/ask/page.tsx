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
import RepositoryScopePicker from '@/components/RepositoryScopePicker';
import { useAuth, getAuthHeaders } from '@/contexts/AuthContext';
import { useLanguage } from '@/contexts/LanguageContext';
import type RepoInfo from '@/types/repoinfo';

interface IndexedProject {
  id: number | null;
  name: string;
  path_with_namespace: string;
  description: string;
  last_activity_at: string;
  web_url: string;
  avatar_url: string;
  indexed_at: string;
  index_status: string;
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
          .filter((p: IndexedProject) => p.index_status === 'indexed')
          .map((p: IndexedProject) => p.path_with_namespace);
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

  const indexedProjects = projects.filter((p) => p.index_status === 'indexed');

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
        <RepositoryScopePicker projects={indexedProjects} selectedRepos={selectedRepos}
          setSelectedRepos={setSelectedRepos} loading={loading} />

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
