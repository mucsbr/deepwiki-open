// UI/transport fixtures only. This does not validate business-analysis accuracy.
const assert = require('node:assert/strict');
const http = require('node:http');
const { randomUUID } = require('node:crypto');
const { chromium } = require('playwright');

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function main() {
  let settings = { enabled: false, revision: 0, groups: [] };
  const sessions = new Map();
  const runs = new Map();
  const publicRequests = [];
  const repo = project => ({ project, url: `https://gitlab.example/${project}`, commit: 'a'.repeat(40) });
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://fixture');
    const json = (data, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data)); };
    let raw = '';
    for await (const part of req) raw += part;
    const body = raw ? JSON.parse(raw) : {};
    if (url.pathname === '/auth/me') return json({ gitlab_user_id: 1, username: 'qa', name: 'QA', is_admin: true });
    if (url.pathname === '/lang/config') return json({ supported_languages: { zh: '中文', en: 'English' }, default: 'zh' });
    if (url.pathname === '/api/admin/stats') return json({ total_indexed_projects: 3, status_counts: { indexed: 3 }, total_wiki_caches: 3, indexed_without_wiki: 0, disk_usage: { repos_mb: 1, databases_mb: 1, wikicache_mb: 1 }, last_batch_run: null });
    if (url.pathname === '/api/admin/projects') return json(['bas/orders', 'easm/tasks', 'private/secret'].map((path, index) => ({ path, project_id: index + 1, status: 'indexed', indexed_at: '2026-10-12T00:00:00Z', last_activity_at: '', repo_path: path, has_wiki_cache: true, wiki_languages: ['zh'] })));
    if (url.pathname === '/api/admin/batch-index/status') return json({ running: false, operation: '', progress: {}, last_result: {}, last_run: null });
    if (url.pathname === '/api/admin/config') return json({ gitlab_url: 'https://gitlab.example', embedder_type: 'openai', batch_groups: '', permission_cache_ttl: 300, admin_usernames: ['qa'] });
    if (url.pathname === '/api/admin/products') return json([]);
    if (url.pathname === '/api/admin/public-ask') {
      assert.equal(req.headers.authorization, 'Bearer fixture-admin');
      if (req.method === 'PUT') settings = { ...body, revision: settings.revision + 1 };
      return json(settings);
    }
    if (url.pathname === '/api/public-agent/config') return json({ enabled: settings.enabled,
      groups: settings.enabled ? settings.groups.filter(g => g.enabled).map(g => ({ id: g.id, name: g.name, description: g.description, repo_count: g.repos.length })) : [],
      defaultProvider: 'fixture', defaultModel: 'transport-only', maxConcurrentRuns: 4 });
    if (url.pathname.startsWith('/api/public-agent/')) {
      publicRequests.push({ path: url.pathname, authorization: req.headers.authorization, key: req.headers['x-public-ask-visitor'], body });
      const key = req.headers['x-public-ask-visitor'];
      if (!/^[a-f0-9]{64}$/.test(key ?? '')) return json({ detail: 'missing visitor' }, 422);
      if (url.pathname === '/api/public-agent/sessions') {
        if (req.method === 'POST') {
          assert.equal(body.repos, undefined);
          const groups = settings.groups.filter(g => body.groups.includes(g.id) && g.enabled);
          if (groups.length !== body.groups.length) return json({ detail: 'Group unavailable' }, 403);
          const value = { id: randomUUID(), key, title: '', repos: [...new Set(groups.flatMap(g => g.repos))].map(repo), language: body.language,
            public_groups: body.groups, system_prompt: body.system_prompt, updated_at: new Date().toISOString(), runs: [], documents: [] };
          sessions.set(value.id, value); return json(value, 201);
        }
        return json({ sessions: [...sessions.values()].filter(s => s.key === key), next_offset: null });
      }
      const sessionMatch = url.pathname.match(/^\/api\/public-agent\/sessions\/([^/]+)(\/runs)?$/);
      if (sessionMatch) {
        const session = sessions.get(sessionMatch[1]);
        if (!session || session.key !== key) return json({ detail: 'Conversation not found' }, 404);
        if (!sessionMatch[2]) return json(session);
        session.title ||= body.message;
        session.system_prompt = body.system_prompt;
        const run = { id: randomUUID(), session_id: session.id, message: body.message, provider: 'fixture', model: 'transport-only', status: 'queued', answer: '', repos: session.repos,
          activity: { events: [{ id: 1, type: 'status', data: { status: 'queued' } }], cursor: 1 } };
        runs.set(run.id, run); session.runs.push(run); return json(run, 202);
      }
      const eventMatch = url.pathname.match(/^\/api\/public-agent\/runs\/([^/]+)\/events$/);
      if (eventMatch) {
        const run = runs.get(eventMatch[1]);
        const session = run && sessions.get(run.session_id);
        if (!session || session.key !== key) return json({ detail: 'Run not found' }, 404);
        res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache, no-transform' });
        const events = [
          { id: 2, type: 'status', data: { status: 'running' } },
          { id: 3, type: 'draft', data: { id: 'answer', content: '正在检查已选分组的源码。' } },
          { id: 4, type: 'tool_start', data: { id: 'read', name: 'read_source', input: { repo: session.repos[0].project, path: 'main.go' } } },
          { id: 5, type: 'tool_end', data: { id: 'read', error: false, result: { lines: 10 } } },
          { id: 6, type: 'text', data: { id: 'answer', content: `UI_TRANSPORT_FIXTURE: ${run.message}` } },
          { id: 7, type: 'status', data: { status: 'completed' } },
        ];
        for (const event of events.filter(e => e.id > Number(url.searchParams.get('after') ?? 0))) {
          await sleep(100);
          run.activity.events.push(event); run.activity.cursor = event.id;
          if (event.type === 'status') run.status = event.data.status;
          if (event.type === 'text') run.answer = event.data.content;
          res.write(`id: ${event.id}\ndata: ${JSON.stringify(event)}\n\n`);
        }
        res.end(); return;
      }
    }
    return json({ detail: 'Unknown fixture route' }, 404);
  });
  await new Promise(resolve => server.listen(18081, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
    const adminContext = await browser.newContext();
    await adminContext.addInitScript(() => localStorage.setItem('deepwiki_jwt', 'fixture-admin'));
    const admin = await adminContext.newPage();
    await admin.goto('http://127.0.0.1:3107/admin');
    await admin.getByRole('button', { name: '免登录 Ask', exact: true }).click();
    for (const [name, path] of [['bas', 'bas/orders'], ['easm', 'easm/tasks']]) {
      await admin.getByRole('button', { name: '＋ 添加分组', exact: true }).click();
      await admin.getByRole('textbox', { name: '分组名称', exact: true }).fill(name);
      await admin.getByPlaceholder(/Filter repositories|搜索仓库/).fill(path);
      await admin.getByRole('checkbox', { name: new RegExp(path.split('/')[1]) }).check();
    }
    await admin.getByRole('checkbox', { name: '启用免登录访问', exact: true }).check();
    await admin.getByRole('button', { name: '保存公开设置', exact: true }).click();
    await admin.getByText(/已保存。访客只能选择/).waitFor();
    assert.deepEqual(settings.groups.map(g => g.repos), [['bas/orders'], ['easm/tasks']]);
    if (process.env.PUBLIC_QA_DIR) await admin.screenshot({ path: `${process.env.PUBLIC_QA_DIR}/admin-public-ask.png`, fullPage: true });

    const contextA = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
    await contextA.addInitScript(() => localStorage.setItem('deepwiki_jwt', 'signed-user-fixture'));
    const a = await contextA.newPage();
    await a.goto('http://127.0.0.1:3107/ask/public');
    await a.getByRole('checkbox', { name: /bas/ }).check();
    await a.getByRole('checkbox', { name: /easm/ }).check();
    assert.equal(await a.getByText('private/secret', { exact: true }).count(), 0);
    await a.locator('summary').filter({ hasText: '自定义系统提示词' }).click();
    const prompt = '你是流程分析师。使用中文输出 {步骤}，并标明证据。';
    await a.getByRole('textbox', { name: '自定义系统提示词', exact: true }).fill(prompt);
    await a.locator('textarea[id^="agent-question-"]').fill('A-功能入口');
    await a.getByRole('button', { name: /^(发送|Send)$/ }).click();
    await a.getByText('UI_TRANSPORT_FIXTURE: A-功能入口', { exact: true }).waitFor();
    assert.equal([...sessions.values()].find(s => s.title === 'A-功能入口').system_prompt, prompt);
    assert.equal(await a.getByText('read_source', { exact: false }).count() > 0, true);
    if (process.env.PUBLIC_QA_DIR) await a.screenshot({ path: `${process.env.PUBLIC_QA_DIR}/public-ask.png`, fullPage: true });

    const contextB = await browser.newContext();
    const b = await contextB.newPage();
    await b.goto('http://127.0.0.1:3107/ask/public');
    await b.getByRole('button', { name: /^(历史会话|History)$/ }).click();
    assert.equal(await b.getByRole('button', { name: /A-功能入口/ }).count(), 0);
    await b.getByRole('checkbox', { name: /easm/ }).check();
    await b.locator('textarea[id^="agent-question-"]').fill('B-业务规则');
    await b.getByRole('button', { name: /^(发送|Send)$/ }).click();
    await b.getByText('UI_TRANSPORT_FIXTURE: B-业务规则', { exact: true }).waitFor();
    const aSession = [...sessions.values()].find(s => s.title === 'A-功能入口');
    const bSession = [...sessions.values()].find(s => s.title === 'B-业务规则');
    assert.notEqual(aSession.key, bSession.key);
    assert.deepEqual(bSession.repos.map(r => r.project), ['easm/tasks']);

    await a.reload();
    await a.getByRole('button', { name: /^(历史会话|History)$/ }).click();
    assert.equal(await a.getByRole('button', { name: /B-业务规则/ }).count(), 0);
    await a.getByRole('button', { name: /A-功能入口/ }).click();
    await a.getByText('UI_TRANSPORT_FIXTURE: A-功能入口', { exact: true }).waitFor();
    await a.locator('summary').filter({ hasText: '自定义系统提示词' }).click();
    assert.equal(await a.getByRole('textbox', { name: '自定义系统提示词', exact: true }).inputValue(), prompt);
    assert.ok(publicRequests.every(r => r.authorization === undefined));
    console.log('PASS: admin scope publishing, login-free group selection, custom prompt, timeline, independent visitor histories, replay and no JWT forwarding.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
