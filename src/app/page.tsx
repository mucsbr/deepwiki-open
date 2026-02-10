'use client';

import React, { useState, useEffect, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { FaWikipediaW, FaGitlab, FaSearch, FaSignOutAlt, FaBookOpen } from 'react-icons/fa';
import ThemeToggle from '@/components/theme-toggle';
import { useLanguage } from '@/contexts/LanguageContext';
import { useAuth, getAuthHeaders } from '@/contexts/AuthContext';

interface GitLabProject {
  id: number;
  name: string;
  path_with_namespace: string;
  description: string | null;
  last_activity_at: string;
  web_url: string;
  avatar_url: string | null;
  indexed_at: string;
  index_status: string;
}

export default function Home() {
  const router = useRouter();
  const { language, setLanguage, messages, supportedLanguages } = useLanguage();
  const { user, token, isAuthenticated, isLoading: authLoading, login, logout } = useAuth();

  // Translation function
  const t = (key: string, params: Record<string, string | number> = {}): string => {
    const keys = key.split('.');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let value: any = messages;
    for (const k of keys) {
      if (value && typeof value === 'object' && k in value) {
        value = value[k];
      } else {
        return key;
      }
    }
    if (typeof value === 'string') {
      return Object.entries(params).reduce((acc: string, [paramKey, paramValue]) => {
        return acc.replace(`{${paramKey}}`, String(paramValue));
      }, value);
    }
    return key;
  };

  // Projects state
  const [projects, setProjects] = useState<GitLabProject[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  // Fetch accessible & indexed projects
  useEffect(() => {
    if (!isAuthenticated || !token) return;

    const fetchProjects = async () => {
      setProjectsLoading(true);
      try {
        const resp = await fetch('/api/projects', {
          headers: getAuthHeaders(token),
        });
        if (resp.ok) {
          const data = await resp.json();
          setProjects(data);
        }
      } catch (err) {
        console.error('Failed to fetch projects:', err);
      } finally {
        setProjectsLoading(false);
      }
    };

    fetchProjects();
  }, [isAuthenticated, token]);

  // Filtered projects
  const filteredProjects = useMemo(() => {
    if (!searchQuery.trim()) return projects;
    const q = searchQuery.toLowerCase();
    return projects.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        p.path_with_namespace.toLowerCase().includes(q) ||
        (p.description && p.description.toLowerCase().includes(q))
    );
  }, [projects, searchQuery]);

  // Navigate to wiki page for a project
  const handleProjectClick = (project: GitLabProject) => {
    const parts = project.path_with_namespace.split('/');
    const owner = parts.slice(0, -1).join('/');
    const repo = parts[parts.length - 1];
    const params = new URLSearchParams();
    params.append('type', 'gitlab');
    params.append('repo_url', encodeURIComponent(project.web_url));
    params.append('language', language);
    router.push(`/${owner}/${repo}?${params.toString()}`);
  };

  // --- Render ---

  // Loading state
  if (authLoading) {
    return (
      <div className="h-screen flex items-center justify-center bg-[var(--background)]">
        <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-[var(--accent-primary)]"></div>
      </div>
    );
  }

  // Not authenticated — show login page
  if (!isAuthenticated) {
    return (
      <div className="h-screen paper-texture p-4 md:p-8 flex flex-col">
        <div className="flex-1 flex items-center justify-center">
          <div className="max-w-md w-full bg-[var(--card-bg)] rounded-lg shadow-custom border border-[var(--border-color)] p-8 text-center">
            <div className="bg-[var(--accent-primary)] p-3 rounded-lg inline-block mb-4">
              <FaWikipediaW className="text-4xl text-white" />
            </div>
            <h1 className="text-2xl font-bold text-[var(--accent-primary)] mb-2">DeepWiki</h1>
            <p className="text-[var(--muted)] text-sm mb-8">Enterprise GitLab Wiki Generator</p>

            <button
              onClick={login}
              className="w-full flex items-center justify-center gap-3 px-6 py-3 rounded-lg bg-[#FC6D26] hover:bg-[#E24329] text-white font-medium transition-colors"
            >
              <FaGitlab className="text-xl" />
              Sign in with GitLab
            </button>
          </div>
        </div>
        <footer className="max-w-6xl mx-auto flex justify-center w-full">
          <ThemeToggle />
        </footer>
      </div>
    );
  }

  // Authenticated — show project list
  return (
    <div className="h-screen paper-texture p-4 md:p-8 flex flex-col">
      <header className="max-w-6xl mx-auto mb-6 h-fit w-full">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 bg-[var(--card-bg)] rounded-lg shadow-custom border border-[var(--border-color)] p-4">
          {/* Logo + title */}
          <div className="flex items-center">
            <div className="bg-[var(--accent-primary)] p-2 rounded-lg mr-3">
              <FaWikipediaW className="text-2xl text-white" />
            </div>
            <div className="mr-6">
              <h1 className="text-xl md:text-2xl font-bold text-[var(--accent-primary)]">{t('common.appName')}</h1>
              <p className="text-xs text-[var(--muted)] whitespace-nowrap">Enterprise GitLab Wiki</p>
            </div>
          </div>

          {/* Search bar */}
          <div className="flex-1 max-w-xl">
            <div className="relative">
              <FaSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted)]" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search repositories..."
                className="input-japanese block w-full pl-10 pr-3 py-2.5 border-[var(--border-color)] rounded-lg bg-transparent text-[var(--foreground)] focus:outline-none focus:border-[var(--accent-primary)]"
              />
            </div>
          </div>

          {/* User info + actions */}
          <div className="flex items-center gap-3">
            {/* Language selector */}
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              className="text-xs px-2 py-1.5 rounded border border-[var(--border-color)] bg-transparent text-[var(--foreground)]"
            >
              {Object.entries(supportedLanguages).map(([key, value]) => (
                <option key={key} value={key}>{value}</option>
              ))}
            </select>

            {/* User avatar + name */}
            <div className="flex items-center gap-2">
              {user?.avatar_url && (
                <img
                  src={user.avatar_url}
                  alt={user.name}
                  className="w-7 h-7 rounded-full border border-[var(--border-color)]"
                />
              )}
              <span className="text-sm text-[var(--foreground)] hidden sm:inline">{user?.name}</span>
            </div>

            <button
              onClick={logout}
              title="Logout"
              className="p-2 text-[var(--muted)] hover:text-[var(--highlight)] transition-colors"
            >
              <FaSignOutAlt />
            </button>
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="flex-1 max-w-6xl mx-auto w-full overflow-y-auto">
        <div className="min-h-full bg-[var(--card-bg)] rounded-lg shadow-custom card-japanese p-6">
          {/* Section header */}
          <div className="flex items-center gap-3 mb-6">
            <FaBookOpen className="text-xl text-[var(--accent-primary)]" />
            <h2 className="text-lg font-bold text-[var(--foreground)] font-serif">
              Repositories ({filteredProjects.length})
            </h2>
          </div>

          {projectsLoading ? (
            <div className="flex justify-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-[var(--accent-primary)]"></div>
            </div>
          ) : filteredProjects.length === 0 ? (
            <div className="text-center py-12 text-[var(--muted)]">
              {projects.length === 0 ? (
                <p>No indexed repositories found. Run batch indexer first.</p>
              ) : (
                <p>No repositories match your search.</p>
              )}
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {filteredProjects.map((project) => (
                <button
                  key={project.id}
                  onClick={() => handleProjectClick(project)}
                  className="text-left p-4 rounded-lg border border-[var(--border-color)] hover:border-[var(--accent-primary)]/50 bg-[var(--background)]/50 hover:bg-[var(--accent-primary)]/5 transition-all group"
                >
                  <div className="flex items-start gap-3">
                    {project.avatar_url ? (
                      <img
                        src={project.avatar_url}
                        alt={project.name}
                        className="w-10 h-10 rounded-md border border-[var(--border-color)]"
                      />
                    ) : (
                      <div className="w-10 h-10 rounded-md bg-[var(--accent-primary)]/10 flex items-center justify-center">
                        <FaGitlab className="text-[var(--accent-primary)]" />
                      </div>
                    )}
                    <div className="flex-1 min-w-0">
                      <h3 className="font-medium text-[var(--foreground)] group-hover:text-[var(--accent-primary)] transition-colors truncate">
                        {project.name}
                      </h3>
                      <p className="text-xs text-[var(--muted)] truncate">
                        {project.path_with_namespace}
                      </p>
                    </div>
                  </div>
                  {project.description && (
                    <p className="text-xs text-[var(--muted)] mt-2 line-clamp-2">
                      {project.description}
                    </p>
                  )}
                  <div className="flex items-center justify-between mt-3 text-xs text-[var(--muted)]">
                    <span>
                      Indexed: {new Date(project.indexed_at).toLocaleDateString()}
                    </span>
                    <span className={`px-1.5 py-0.5 rounded text-xs ${
                      project.index_status === 'indexed'
                        ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                        : 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400'
                    }`}>
                      {project.index_status}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </main>

      <footer className="max-w-6xl mx-auto mt-8 flex flex-col gap-4 w-full">
        <div className="flex flex-col sm:flex-row justify-between items-center gap-4 bg-[var(--card-bg)] rounded-lg p-4 border border-[var(--border-color)] shadow-custom">
          <p className="text-[var(--muted)] text-sm font-serif">{t('footer.copyright')}</p>
          <div className="flex items-center gap-4">
            <Link
              href="/wiki/projects"
              className="text-xs text-[var(--muted)] hover:text-[var(--accent-primary)] transition-colors"
            >
              All Wiki Projects
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
