const fs = require('node:fs');
const assert = require('node:assert/strict');
const { test } = require('node:test');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => {
  const { outputText } = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  module._compile(outputText, filename);
};
const { agentRequest, streamAgentEvents, publicVisitorKey } = require('../../src/utils/agentClient.ts');

test('public requests never forward the logged-in JWT or use private Ask routes', async () => {
  let captured;
  global.fetch = async (url, options) => { captured = { url, options }; return { ok: true, json: async () => ({}) }; };
  const visitorKey = 'a'.repeat(64);
  await agentRequest({ visitorKey }, '/sessions', { groups: ['bas'] });
  assert.equal(captured.url, '/api/public-agent/sessions');
  assert.equal(captured.options.headers['X-Public-Ask-Visitor'], visitorKey);
  assert.equal(captured.options.headers.Authorization, undefined);
  await agentRequest('signed-user', '/sessions');
  assert.equal(captured.url, '/api/agent/sessions');
  assert.equal(captured.options.headers.Authorization, 'Bearer signed-user');
});

test('public SSE retains visitor ownership when replaying persisted events', async () => {
  let captured;
  global.fetch = async (url, options) => { captured = { url, options }; return new Response('id: 1\ndata: {"id":1,"type":"status","data":{"status":"queued"}}\n\n'); };
  const events = [];
  await streamAgentEvents({ visitorKey: 'b'.repeat(64) }, 'run', 10, new AbortController().signal, event => events.push(event));
  assert.equal(captured.url, '/api/public-agent/runs/run/events?after=10');
  assert.equal(captured.options.headers.Authorization, undefined);
  assert.equal(events[0].data.status, 'queued');
});

test('browser visitor identity persists without a login or insecure-origin randomUUID', () => {
  const data = new Map();
  global.localStorage = { getItem: key => data.get(key), setItem: (key, value) => data.set(key, value) };
  const first = publicVisitorKey();
  assert.match(first, /^[a-f0-9]{64}$/);
  assert.equal(publicVisitorKey(), first);
  data.clear();
  assert.notEqual(publicVisitorKey(), first);
});
