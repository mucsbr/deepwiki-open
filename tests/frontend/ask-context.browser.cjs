// Transport/state fixtures, not business-analysis accuracy or live model tests.
const assert = require('node:assert/strict');
const http = require('node:http');
const { chromium } = require('playwright');

async function main() {
  const repo = { project: 'fixture/demo', url: 'https://gitlab.example/fixture/demo', commit: 'a'.repeat(40) };
  const session = { id: 'fixture-session', title: 'Context controls fixture', repos: [repo], public_groups: ['bas'], system_prompt: '', language: 'zh', updated_at: new Date().toISOString() };
  const runs = [{ id: 'original', session_id: session.id, kind: 'analysis', message: 'FIXTURE: original question', provider: 'fixture', model: 'transport-only', status: 'running', answer: '' }];
  const events = new Map([['original', [{ id: 1, type: 'context', data: { input_tokens: 28000, budget_tokens: 32000, source: 'reported' } }]]]);
  const streams = new Map();
  const corrections = [];
  let sequence = 1;
  const publish = (rid, type, data) => {
    const event = { id: ++sequence, type, data };
    events.get(rid).push(event);
    if (type === 'context') session.context = data;
    for (const stream of streams.get(rid) || []) {
      stream.write(`data: ${JSON.stringify(event)}\n\n`);
      if (type === 'status' && data.status === 'completed') stream.end();
    }
  };
  const detail = () => ({ ...session, documents: [], runs: runs.map(run => ({ ...run, activity: { events: events.get(run.id), cursor: events.get(run.id).at(-1)?.id || 0 } })) });
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, 'http://fixture');
    const json = (value, status = 200) => { response.writeHead(status, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(value)); };
    let raw = '';
    for await (const chunk of request) raw += chunk;
    const body = raw ? JSON.parse(raw) : {};
    if (url.pathname === '/lang/config') return json({ supported_languages: { zh: '中文' }, default: 'zh' });
    if (url.pathname === '/api/public-agent/config') return json({ enabled: true, groups: [{ id: 'bas', name: 'bas', repo_count: 1 }], defaultProvider: 'fixture', defaultModel: 'transport-only', maxConcurrentRuns: 4 });
    if (url.pathname === '/api/public-agent/sessions') return json({ sessions: [session], next_offset: null });
    if (url.pathname === `/api/public-agent/sessions/${session.id}`) return json(detail());
    if (url.pathname === '/api/public-agent/runs/original/cancel') { runs[0].status = 'cancelled'; return json(runs[0]); }
    if (url.pathname === '/api/public-agent/runs/original/resume') {
      runs[0].status = 'running';
      setTimeout(() => publish('original', 'context', { input_tokens: 7000, budget_tokens: 32000, source: 'reported' }), 600);
      return json(runs[0], 202);
    }
    if (url.pathname === `/api/public-agent/sessions/${session.id}/runs` || url.pathname === `/api/public-agent/sessions/${session.id}/compact`) {
      const compact = url.pathname.endsWith('/compact');
      if (!compact) corrections.push(body.message);
      const run = { id: `fixture-${runs.length}`, session_id: session.id, kind: compact ? 'compact' : 'analysis', message: compact ? 'compact' : body.message, status: 'running', answer: '', provider: 'fixture', model: 'transport-only' };
      runs.push(run); events.set(run.id, []);
      setTimeout(() => {
        if (compact) {
          publish(run.id, 'compaction', { id: run.id, phase: 'running', manual: true });
          publish(run.id, 'compaction', { id: run.id, phase: 'completed', manual: true, before_tokens: 28000, after_tokens: 5000 });
          publish(run.id, 'context', { input_tokens: 5000, budget_tokens: 32000, source: 'estimated' });
        } else {
          run.answer = 'FIXTURE: correction accepted';
          publish(run.id, 'text', { id: run.id, content: run.answer });
        }
        run.status = 'completed'; publish(run.id, 'status', { status: 'completed' });
      }, 600);
      return json(run, 202);
    }
    const streamMatch = url.pathname.match(/^\/api\/public-agent\/runs\/([^/]+)\/events$/);
    if (streamMatch) {
      const rid = streamMatch[1];
      response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache, no-transform' });
      const listeners = streams.get(rid) || new Set(); streams.set(rid, listeners); listeners.add(response);
      for (const event of events.get(rid).filter(event => event.id > Number(url.searchParams.get('after')))) response.write(`data: ${JSON.stringify(event)}\n\n`);
      response.on('close', () => listeners.delete(response));
      if (runs.find(run => run.id === rid).status === 'completed') response.end();
      return;
    }
    return json({ detail: 'Unknown fixture route' }, 404);
  });
  await new Promise(resolve => server.listen(18081, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || undefined, headless: true });
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
    await page.goto('http://127.0.0.1:3107/ask/public');
    await page.getByRole('button', { name: '历史会话', exact: true }).click();
    await page.getByRole('button', { name: /Context controls fixture/ }).click();
    const context = page.getByRole('complementary', { name: '上下文', exact: true });
    await context.getByText(/28,000 \/ 32,000/).waitFor();
    const compact = page.getByRole('button', { name: '压缩上下文', exact: true });
    assert.equal(await compact.isEnabled(), false);
    const question = page.locator('textarea[id^="agent-question-"]');
    await question.fill('FIXTURE: report the missing backend and keep prior evidence');
    await page.getByRole('button', { name: '停止', exact: true }).click();
    assert.equal(await question.inputValue(), 'FIXTURE: report the missing backend and keep prior evidence');
    await compact.click();
    await context.getByText(/5,000 \/ 32,000/).waitFor();
    await page.getByRole('button', { name: '原样继续分析', exact: true }).click();
    await context.getByText(/7,000 \/ 32,000/).waitFor();
    await page.getByRole('button', { name: '停止', exact: true }).click();
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await page.getByText('FIXTURE: correction accepted', { exact: true }).waitFor();
    assert.deepEqual(corrections, ['FIXTURE: report the missing backend and keep prior evidence']);
    assert.equal(await page.getByRole('button', { name: '原样继续分析', exact: true }).count(), 0);
    await compact.click();
    await context.getByText(/5,000 \/ 32,000/).waitFor();
    await page.getByText(/手动压缩上下文.*28,000.*5,000/).last().waitFor();
    assert.equal(await page.getByText('FIXTURE: original question', { exact: true }).count(), 1);
    assert.equal(await page.getByText('FIXTURE: correction accepted', { exact: true }).count(), 1);
    await page.reload();
    await page.getByRole('button', { name: '历史会话', exact: true }).click();
    await page.getByRole('button', { name: /Context controls fixture/ }).click();
    await context.getByText(/5,000 \/ 32,000/).waitFor();
    if (process.env.CONTEXT_QA_DIR) await page.screenshot({ path: `${process.env.CONTEXT_QA_DIR}/context-controls.png`, fullPage: true });
    console.log('PASS: stop/correct/send in the same conversation, context labels, compaction replay and original evidence retained.');
  } finally {
    if (browser) await browser.close();
    for (const listeners of streams.values()) for (const response of listeners) response.end();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
