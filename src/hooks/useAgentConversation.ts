'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { agentRequest, agentRequestId, streamAgentEvents } from '@/utils/agentClient';
import { isActive, type AgentDocument, type AgentEvent, type AgentRun, type AgentSession, type SessionDetail, type ToolProgress } from '@/components/agent/types';

export function useAgentConversation(token: string | null) {
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [tools, setTools] = useState<ToolProgress[]>([]);
  const [todos, setTodos] = useState<{ content: string; status: string }[]>([]);
  const [reconnecting, setReconnecting] = useState(false);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const controller = useRef<AbortController | null>(null);
  const selected = useRef<string | null>(null);

  const refreshHistory = useCallback(async (offset = 0) => {
    if (!token) return;
    const data = await agentRequest<{ sessions: AgentSession[]; next_offset: number | null }>(token, `/sessions?offset=${offset}`);
    setSessions(previous => offset ? [...previous, ...data.sessions] : data.sessions);
    setNextOffset(data.next_offset);
  }, [token]);

  const reset = useCallback(() => {
    controller.current?.abort(); selected.current = null;
    setSession(null); setTools([]); setTodos([]); setError(''); setReconnecting(false);
  }, []);

  useEffect(() => {
    reset(); setSessions([]);
    if (token) refreshHistory().catch(e => setError(e.message));
    return () => controller.current?.abort();
  }, [token, refreshHistory, reset]);

  const connect = useCallback(async (sid: string, rid: string) => {
    if (!token) return;
    controller.current?.abort();
    const abort = new AbortController(); controller.current = abort;
    setTools([]); setTodos([]);
    let cursor = 0; let failures = 0;
    while (!abort.signal.aborted && selected.current === sid) {
      try {
        await streamAgentEvents(token, rid, cursor, abort.signal, (event: AgentEvent) => {
          if (abort.signal.aborted || selected.current !== sid || event.id <= cursor) return;
          cursor = event.id; setReconnecting(false);
          if (event.type === 'text' || event.type === 'status') {
            setSession(previous => previous?.id !== sid ? previous : {
              ...previous, runs: previous.runs.map(run => run.id !== rid ? run : {
                ...run, ...(event.type === 'text' ? { answer: event.data.content ?? '' } : {}),
                ...(event.data.status ? { status: event.data.status } : {}),
                ...(event.type === 'status' ? { error: typeof event.data.error === 'string' ? event.data.error : null } : {}),
              }),
            });
          }
          if (event.type === 'tool_start' && event.data.id) {
            const item = { id: event.data.id, name: event.data.name ?? '', done: false, error: false };
            setTools(previous => [...previous, item]);
          }
          if (event.type === 'tool_end') setTools(previous => previous.map(tool => tool.id !== event.data.id ? tool : { ...tool, done: true, error: !!event.data.error }));
          if (event.type === 'plan') setTodos(event.data.todos ?? []);
        });
        const detail = await agentRequest<SessionDetail>(token, `/sessions/${sid}`, undefined, abort.signal);
        if (selected.current !== sid || abort.signal.aborted) return;
        setSession(detail);
        if (!isActive(detail.runs.find(run => run.id === rid)?.status)) { await refreshHistory(); return; }
        // EOF may be a proxy timeout. Reconnect from the last persisted event.
        failures++;
      } catch (e) {
        if (abort.signal.aborted) return;
        failures++;
        if (failures >= 5) { setError(e instanceof Error ? e.message : 'Connection lost'); setReconnecting(false); return; }
      }
      if (failures >= 5) { setError('Connection lost. Reopen the conversation to reconnect.'); setReconnecting(false); return; }
      setReconnecting(true);
      await new Promise(resolve => setTimeout(resolve, Math.min(1000 * failures, 5000)));
    }
  }, [token, refreshHistory]);

  const open = useCallback(async (sid: string) => {
    if (!token) return;
    controller.current?.abort(); selected.current = sid;
    setPending(true); setSession(null); setError(''); setTools([]); setTodos([]);
    try {
      const detail = await agentRequest<SessionDetail>(token, `/sessions/${sid}`);
      if (selected.current !== sid) return;
      setSession(detail);
      const active = detail.runs.find(run => isActive(run.status));
      if (active) void connect(sid, active.id);
    } catch (e) {
      if (selected.current === sid) {
        selected.current = null;
        setError(e instanceof Error ? e.message : 'Unable to open conversation');
      }
    } finally { setPending(false); }
  }, [token, connect]);

  const send = useCallback(async (message: string, repos: string[], language: string, provider: string, model: string) => {
    if (!token || pending) return false;
    setPending(true); setError('');
    let sid = selected.current;
    try {
      if (!sid) {
        const created = await agentRequest<AgentSession>(token, '/sessions', { repos, language });
        sid = created.id; selected.current = sid;
        setSession({ ...created, runs: [], documents: [] });
      }
      const run = await agentRequest<AgentRun>(token, `/sessions/${sid}/runs`, { message, request_id: agentRequestId(), provider, model });
      const detail = await agentRequest<SessionDetail>(token, `/sessions/${sid}`);
      if (selected.current !== sid) return true;
      setSession(detail); void connect(sid, run.id); void refreshHistory().catch(() => {});
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to send message');
      // Recover a possibly accepted POST without submitting a second run.
      if (sid) {
        const detail = await agentRequest<SessionDetail>(token, `/sessions/${sid}`).catch(() => null);
        if (detail && selected.current === sid) {
          setSession(detail);
          const active = detail.runs.find(run => isActive(run.status));
          if (active) { void connect(sid, active.id); return true; }
        }
      }
      return false;
    } finally { setPending(false); }
  }, [token, pending, connect, refreshHistory]);

  const control = useCallback(async (rid: string, action: 'cancel' | 'resume', model: { provider?: string; model?: string } = {}) => {
    if (!token || !selected.current) return;
    const sid = selected.current; setPending(true); setError('');
    try { await agentRequest<AgentRun>(token, `/runs/${rid}/${action}`, model); await open(sid); }
    catch (e) { setError(e instanceof Error ? e.message : 'Run action failed'); }
    finally { setPending(false); }
  }, [token, open]);

  const download = useCallback((document: AgentDocument) => {
    const url = URL.createObjectURL(new Blob([document.content], { type: 'text/markdown;charset=utf-8' }));
    const link = window.document.createElement('a'); link.href = url; link.download = document.name; link.click(); URL.revokeObjectURL(url);
  }, []);

  return { sessions, session, error, pending, tools, todos, reconnecting, nextOffset,
    reset, open, send, control, download, refreshHistory };
}
