/**
 * UI-only fixture test (no business repositories or model calls).
 * Start: SERVER_BASE_URL=http://127.0.0.1:18081 npm run dev -- --port 3107
 * Run with Playwright available: node tests/frontend/ask-timeline.browser.cjs
 */
const assert = require('node:assert/strict');
const http = require('node:http');
const { mkdtempSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const screenshots = mkdtempSync(join(tmpdir(), 'deepwiki-ask-timeline-'));
  const repos = [{ project: 'example/tasks', commit: 'abcdef123456', url: 'https://gitlab.example/example/tasks' }];
  const run = { id: 'r1', session_id: 's1', message: '任务从页面提交后如何处理？（交互测试数据）', provider: 'test', model: 'fixture', status: 'running', answer: '', repos };
  const session = { id: 's1', title: run.message, repos, language: 'zh', updated_at: new Date().toISOString() };
  const events = [];
  const streams = new Set();
  const cursors = [];
  const publish = (type, data) => {
    const event = { id: events.length + 1, type, data };
    events.push(event);
    for (const stream of streams) stream.write(`data: ${JSON.stringify(event)}\n\n`);
  };
  publish('draft', { id: 'm1', content: '我会先查看页面提交入口，再追踪后端接收位置。', done: true });
  publish('plan', { todos: [{ content: '查找页面入口', status: 'completed' }, { content: '追踪后端调用', status: 'in_progress' }] });
  for (let i = 0; i < 18; i++) {
    publish('tool_start', { id: `tool-${i}`, name: 'read_source', input: { repo: 'example/tasks', path: `src/pages/tasks/entry-${i}.tsx`, start_line: 1, end_line: 80 } });
    publish('tool_end', { id: `tool-${i}`, result: { lines: 80 }, error: false });
  }
  publish('draft', { id: 'm2', content: '已找到提交入口，正在核对后端接收路由。', done: true });
  publish('tool_start', { id: 'search', name: 'search_source', input: { repo: 'example/tasks', query: '/api/tasks' } });
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const json = value => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)); };
    if (url.pathname === '/auth/me') return json({ gitlab_user_id: 1, name: 'UI Test', username: 'test', is_admin: false });
    if (url.pathname === '/lang/config') return json({ supported_languages: { zh: '中文', en: 'English' }, default: 'zh' });
    if (url.pathname === '/api/projects') return json([{ id: 1, name: 'tasks', path_with_namespace: 'example/tasks', index_status: 'indexed' }]);
    if (url.pathname === '/api/agent/config') return json({ defaultProvider: 'test', defaultModel: 'fixture' });
    if (url.pathname === '/api/agent/sessions') return json({ sessions: [session], next_offset: null });
    if (url.pathname === '/api/agent/sessions/s1') return json({ ...session, runs: [{ ...run, activity: { events, cursor: events.length } }], documents: [] });
    if (url.pathname === '/api/agent/runs/r1/events') {
      const after = Number(url.searchParams.get('after'));
      cursors.push(after);
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no' });
      res.write(': connected\n\n');
      for (const event of events.filter(item => item.id > after)) res.write(`data: ${JSON.stringify(event)}\n\n`);
      if (run.status !== 'running') return res.end();
      streams.add(res);
      req.on('close', () => streams.delete(res));
      return;
    }
    res.statusCode = 404; json({ detail: 'Unknown fixture route' });
  });
  await new Promise(resolve => server.listen(18081, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => { localStorage.setItem('deepwiki_jwt', 'ui-test-only'); localStorage.setItem('language', 'zh'); });
    await page.goto('http://127.0.0.1:3107/ask');
    await page.getByRole('button', { name: '历史会话', exact: true }).click();
    await page.getByRole('button', { name: new RegExp('任务从页面提交后') }).click();
    const timeline = page.getByRole('region', { name: '对话与执行过程' });
    await timeline.getByText('已找到提交入口，正在核对后端接收路由。', { exact: true }).waitFor();
    for (let i = 0; i < 100 && !streams.size; i++) await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(streams.size, 1, 'SSE attached after history snapshot');
    assert.equal(cursors[0], events.length, 'reconnect starts after replay cursor');
    assert.equal(await timeline.locator('details').filter({ hasText: 'read_source' }).count(), 18);
    // Real incremental SSE through the Next.js proxy, not a preassembled response.
    publish('tool_end', { id: 'search', error: true });
    publish('draft', { id: 'm3', content: '检索服务暂不可用，' });
    await timeline.getByText('检索服务暂不可用，', { exact: true }).waitFor();
    publish('draft', { id: 'm3', content: '检索服务暂不可用，改为读取已找到的路由文件。', done: true });
    await timeline.getByText('检索服务暂不可用，改为读取已找到的路由文件。', { exact: true }).waitFor();
    assert.equal(await timeline.getByText('我会先查看页面提交入口，再追踪后端接收位置。', { exact: true }).count(), 1);
    assert.equal(await timeline.getByText('工具调用失败').count(), 1);
    await page.getByRole('complementary', { name: '当前计划' }).getByText('追踪后端调用', { exact: false }).waitFor();
    await page.waitForTimeout(250);
    assert.ok(await timeline.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight < 64), 'follows new output');
    await page.screenshot({ path: join(screenshots, 'desktop-live.png'), fullPage: true });
    await timeline.evaluate(el => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); });
    await page.getByRole('button', { name: '↓ 回到最新进度' }).waitFor();
    publish('draft', { id: 'm4', content: '继续检查边界条件。' });
    await timeline.getByText('继续检查边界条件。', { exact: true }).waitFor();
    assert.equal(await timeline.evaluate(el => el.scrollTop), 0, 'does not steal scroll while reading earlier activity');
    await page.screenshot({ path: join(screenshots, 'desktop-plan.png'), fullPage: true });
    await page.getByRole('button', { name: '↓ 回到最新进度' }).click();
    const countBefore = cursors.length;
    for (const stream of streams) stream.end();
    for (let i = 0; i < 100 && cursors.length === countBefore; i++) await new Promise(resolve => setTimeout(resolve, 100));
    assert.ok(cursors.length > countBefore, 'reconnects after EOF');
    assert.equal(await timeline.locator('details').filter({ hasText: 'read_source' }).count(), 18, 'no replay duplicates');
    publish('plan', { todos: [{ content: '查找页面入口', status: 'completed' }, { content: '追踪后端调用', status: 'completed' }] });
    publish('draft', { id: 'final', content: '最终结果：页面入口连接到任务接口。' });
    publish('text', { id: 'final', content: '最终结果：页面入口连接到任务接口。' });
    run.answer = '最终结果：页面入口连接到任务接口。'; run.status = 'completed';
    publish('status', { status: 'completed' });
    for (const stream of streams) stream.end();
    await timeline.getByText('回答', { exact: true }).waitFor();
    assert.equal(await timeline.getByText(run.answer, { exact: true }).count(), 1, 'no duplicate final draft');
    await page.reload();
    await page.getByRole('button', { name: '历史会话', exact: true }).click();
    await page.getByRole('button', { name: new RegExp('任务从页面提交后') }).click();
    await timeline.getByText(run.answer, { exact: true }).waitFor();
    assert.equal(await timeline.getByText('我会先查看页面提交入口，再追踪后端接收位置。', { exact: true }).count(), 1, 'completed history retains progress');
    await page.setViewportSize({ width: 390, height: 844 });
    await timeline.scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    await page.screenshot({ path: join(screenshots, 'mobile-completed.png') });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'no mobile horizontal overflow');
    assert.deepEqual(errors, []);
    console.log(`PASS: incremental SSE, plans, tool errors, scroll follow/pause, reconnect, completed replay, mobile. Screenshots: ${screenshots}`);
  } finally {
    if (browser) await browser.close();
    for (const stream of streams) stream.end();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
