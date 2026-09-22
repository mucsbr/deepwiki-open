import type { AgentEvent } from '@/components/agent/types';

// getRandomValues also works on private HTTP origins where randomUUID is absent.
export function agentRequestId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export async function agentRequest<T>(token: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/agent${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store', signal,
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    const message = Array.isArray(detail.detail)
      ? detail.detail.map((item: { loc?: (string | number)[]; msg?: string }) => `${item.loc?.join('.')}: ${item.msg}`).join('; ')
      : detail.detail;
    throw new Error(typeof message === 'string' ? message : `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

/** Authenticated fetch streaming: EventSource cannot set Bearer headers. */
export async function streamAgentEvents(token: string, runId: string, after: number,
  signal: AbortSignal, onEvent: (event: AgentEvent) => void): Promise<void> {
  const response = await fetch(`/api/agent/runs/${runId}/events?after=${after}`, {
    headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' }, signal, cache: 'no-store',
  });
  if (!response.ok || !response.body) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(typeof detail.detail === 'string' ? detail.detail : `Event stream failed (${response.status})`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary: number;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
        if (data) onEvent(JSON.parse(data) as AgentEvent);
      }
      if (done) break;
    }
  } finally { reader.releaseLock(); }
}
