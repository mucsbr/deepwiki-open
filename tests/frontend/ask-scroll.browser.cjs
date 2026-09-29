// Real browser geometry + SSE fixtures. No production account/model calls.
// SERVER_BASE_URL=http://127.0.0.1:18081 npm exec next -- dev --port 3107
const assert = require('node:assert/strict');
const http = require('node:http');
const { chromium } = require('playwright');

async function main() {
  const events = [];
  const streams = new Set();
  const repo = { project: 'example/tasks', commit: 'abcdef', url: 'https://gitlab.example/example/tasks' };
  const run = { id: 'run', session_id: 'session', message: '滚动回归测试', provider: 'test', model: 'fixture', status: 'running', answer: '', repos: [repo] };
  const session = { id: 'session', title: run.message, repos: [repo], language: 'zh', updated_at: new Date().toISOString() };
  const chart = '先检查入口。\n\n```mermaid\nflowchart TD\n A[Start] --> B[Validate]\n B --> C[Dispatch]\n C --> D[Complete]\n```\n';
  const publish = (type, data) => {
    const event = { id: events.length + 1, type, data };
    events.push(event);
    for (const stream of streams) stream.write(`data: ${JSON.stringify(event)}\n\n`);
  };
  publish('draft', { id: 'diagram', content: chart, done: true });
  for (let i = 0; i < 24; i++) {
    publish('tool_start', { id: `read-${i}`, name: 'read_source', input: { repo: repo.project, path: `src/entry-${i}.ts` } });
    publish('tool_end', { id: `read-${i}`, error: false });
  }
  publish('draft', { id: 'tail', content: 'TAIL_MARKER' });
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const json = data => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)); };
    if (url.pathname === '/auth/me') return json({ gitlab_user_id: 1, username: 'qa', name: 'QA' });
    if (url.pathname === '/lang/config') return json({ supported_languages: { zh: '中文' }, default: 'zh' });
    if (url.pathname === '/api/projects') return json([{ id: 1, name: 'tasks', path_with_namespace: repo.project, index_status: 'indexed' }]);
    if (url.pathname === '/api/agent/config') return json({ defaultProvider: 'test', defaultModel: 'fixture' });
    if (url.pathname === '/api/agent/sessions') return json({ sessions: [session], next_offset: null });
    if (url.pathname === '/api/agent/sessions/session') return json({ ...session, runs: [{ ...run, activity: { events, cursor: events.length } }], documents: [] });
    if (url.pathname === '/api/agent/runs/run/events') {
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache, no-transform' });
      res.write(': connected\n\n');
      for (const event of events.filter(e => e.id > Number(url.searchParams.get('after')))) res.write(`data: ${JSON.stringify(event)}\n\n`);
      streams.add(res); req.on('close', () => streams.delete(res)); return;
    }
    res.statusCode = 404; json({ detail: 'Unknown fixture route' });
  });
  await new Promise(resolve => server.listen(18081, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
    await page.addInitScript(() => localStorage.setItem('deepwiki_jwt', 'fixture-only'));
    await page.goto('http://127.0.0.1:3107/ask');
    await page.getByRole('button', { name: '历史会话', exact: true }).click();
    await page.getByRole('button', { name: /滚动回归测试/ }).click();
    const view = page.getByRole('region', { name: '对话与执行过程' });
    const jump = page.getByRole('button', { name: '↓ 回到最新进度' });
    await view.locator('svg[id^="mermaid-"]').first().waitFor({ state: 'attached' });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(500);
    await view.evaluate(el => { window.qaSvg = el.querySelector('svg[id^="mermaid-"]'); });
    await view.evaluate(el => { el.scrollTop = 0; });
    await jump.waitFor();
    await page.waitForTimeout(500);
    assert.ok(await page.evaluate(() => window.qaSvg.isConnected), 'scroll-state change must not remount existing Mermaid diagrams');
    const pageHeight = await page.evaluate(() => document.documentElement.scrollHeight);
    await jump.click();
    await page.waitForTimeout(600);
    const gap = () => view.evaluate(el => el.scrollHeight - el.clientHeight - el.scrollTop);
    assert.ok(await gap() <= 2, 'jump must reach the actual bottom (not a 64px near-bottom zone)');
    assert.equal(await page.evaluate(() => document.documentElement.scrollHeight), pageHeight, 'showing/hiding the jump control cannot change page height');
    assert.ok(await view.evaluate(el => el.getBoundingClientRect().bottom <= innerHeight + 2), 'jump reveals the transcript bottom in the outer viewport');

    // A genuine wheel scroll should detach, then reattach exactly at the bottom.
    const box = await view.boundingBox();
    await page.mouse.move(box.x + box.width / 2, Math.max(20, box.y + box.height / 2));
    await page.mouse.wheel(0, -420);
    await jump.waitFor();
    await page.waitForTimeout(250);
    const stopped = await view.evaluate(el => el.scrollTop);
    publish('draft', { id: 'diagram', content: chart + '\n已确认入口，继续检查调用。', done: true });
    publish('draft', { id: 'tail', content: '新段落\n\nTAIL_MARKER' });
    await page.waitForTimeout(600);
    assert.ok(await page.evaluate(() => window.qaSvg.isConnected), 'streaming more text must preserve an unchanged diagram');
    assert.equal(await view.evaluate(el => el.scrollTop), stopped, 'new content must not move a reader who scrolled up');
    await page.mouse.wheel(0, 50000);
    await page.waitForTimeout(600);
    for (let i = 0; i < 5; i++) {
      assert.ok(await gap() <= 2, 'manual bottom must remain at bottom, without bouncing back');
      await page.waitForTimeout(100);
    }
    // Delayed reflow and viewport resize should stick only while following.
    publish('draft', { id: 'tail', content: '更多结果\n\n'.repeat(20) + 'TAIL_MARKER' });
    await page.waitForTimeout(600);
    assert.ok(await gap() <= 2, 'stream growth stays attached to the bottom');
    await page.setViewportSize({ width: 390, height: 700 });
    await page.waitForTimeout(600);
    assert.ok(await gap() <= 2, 'viewport resize keeps the bottom attached');
    await view.evaluate(el => { el.scrollTop = Math.max(0, el.scrollHeight - el.clientHeight - 35); });
    await jump.waitFor();
    const near = await view.evaluate(el => el.scrollTop);
    publish('draft', { id: 'tail', content: '更多结果\n\n'.repeat(22) + 'TAIL_MARKER' });
    await page.waitForTimeout(500);
    assert.equal(await view.evaluate(el => el.scrollTop), near, '35px above bottom is reading history, not following');
    await jump.click();
    await page.waitForTimeout(500);
    assert.ok(await gap() <= 2, 'mobile jump reaches exact bottom');
    console.log('PASS: diagram identity, exact jump, stable page height, wheel/manual bottom, streaming pause/follow, viewport resize, mobile.');
  } finally {
    if (browser) await browser.close();
    for (const stream of streams) stream.end();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
