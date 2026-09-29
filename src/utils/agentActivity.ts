import { isActive, type AgentEvent, type RunActivity } from '../components/agent/types';

export const emptyActivity = (): RunActivity => ({ items: [], cursor: 0 });

/** One ordered timeline per run. Replayed snapshots and reconnects are idempotent. */
export function applyAgentEvent(state: RunActivity, event: AgentEvent): RunActivity {
  if (event.id <= state.cursor) return state;
  const next = { ...state, items: [...state.items], cursor: event.id };
  const data = event.data;
  if (event.type === 'draft' || event.type === 'text') {
    const final = event.type === 'text';
    const matching = final ? next.items.findLast(item => item.kind === 'message' && item.content === data.content) : undefined;
    const id = data.id || matching?.id || next.legacyMessageId || `message-${event.id}`;
    if (!data.id) next.legacyMessageId = id;
    const index = next.items.findIndex(item => item.kind === 'message' && item.id === id);
    const item = { kind: 'message' as const, id, content: data.content ?? '', done: final || !!data.done, final };
    if (index < 0) next.items.push(item);
    else next.items[index] = item;
  } else if (event.type === 'tool_start' && data.id) {
    next.legacyMessageId = undefined;
    if (!next.items.some(item => item.kind === 'tool' && item.id === data.id)) {
      next.items.push({ kind: 'tool', id: data.id, name: data.name ?? '', input: data.input, done: false, error: false });
    }
  } else if (event.type === 'tool_end') {
    next.items = next.items.map(item => item.kind === 'tool' && item.id === data.id
      ? { ...item, done: true, interrupted: false, error: !!data.error, result: data.result } : item);
  } else if (event.type === 'plan') {
    next.items.push({ kind: 'plan', id: `plan-${event.id}`, todos: data.todos ?? [] });
  } else if (event.type === 'refresh') {
    const id = `refresh-${data.repo}-${next.items.filter(item => item.kind === 'status' && isActive(item.status)).length}`;
    const index = next.items.findIndex(item => item.kind === 'refresh' && item.id === id);
    const item = { kind: 'refresh' as const, id, repo: data.repo ?? '', phase: data.phase ?? '' };
    if (index < 0) next.items.push(item);
    else next.items[index] = item;
  } else if (event.type === 'status' && data.status) {
    next.legacyMessageId = undefined;
    // Keep interruption/resumption visible and prevent old tools spinning forever.
    next.items = next.items.map(item => item.kind === 'message' ? { ...item, done: true } : item);
    if (!isActive(data.status)) next.items = next.items.map(item => item.kind === 'tool' && !item.done ? { ...item, interrupted: true } : item);
    if (data.status !== 'completed') next.items.push({ kind: 'status', id: `status-${event.id}`, status: data.status });
  }
  return next;
}

export function replayActivity(activity?: { events: AgentEvent[]; cursor: number }): RunActivity {
  if (!activity) return emptyActivity();
  const state = activity.events.reduce(applyAgentEvent, emptyActivity());
  return { ...state, cursor: activity.cursor };
}
