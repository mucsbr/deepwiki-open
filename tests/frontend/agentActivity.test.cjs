// Uses the repository's TypeScript compiler, with no extra test dependencies.
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
const { emptyActivity, applyAgentEvent, replayActivity } = require('../../src/utils/agentActivity.ts');
const event = (id, type, data) => ({ id, type, data });

test('streamed rounds stay ordered; final answer replaces only its own draft', () => {
  const events = [
    event(1, 'draft', { id: 'a', content: 'Looking' }),
    event(2, 'draft', { id: 'a', content: 'Looking for the entry.', done: true }),
    event(3, 'tool_start', { id: 'read', name: 'read_source', input: { path: 'entry.ts' } }),
    event(4, 'tool_end', { id: 'read', error: false, result: { lines: 10 } }),
    event(5, 'draft', { id: 'b', content: 'The entry' }),
    event(6, 'text', { id: 'b', content: 'The entry is here.' }),
    event(7, 'status', { status: 'completed' }),
  ];
  const state = events.reduce(applyAgentEvent, emptyActivity());
  assert.deepEqual(state.items.map(item => item.kind), ['message', 'tool', 'message']);
  assert.equal(state.items[0].content, 'Looking for the entry.');
  assert.equal(state.items[1].done, true);
  assert.equal(state.items[2].final, true);
  assert.equal(state.items[2].content, 'The entry is here.');
  assert.equal(applyAgentEvent(state, events[4]), state);
  assert.deepEqual(replayActivity({ events, cursor: 7 }), state);
});

test('parallel tool completions match IDs and plans retain each update', () => {
  const state = [
    event(1, 'tool_start', { id: 'a', name: 'read_source' }),
    event(2, 'tool_start', { id: 'b', name: 'search_index' }),
    event(3, 'tool_end', { id: 'b', error: true }),
    event(4, 'plan', { todos: [{ content: 'Trace entry', status: 'in_progress' }] }),
    event(5, 'tool_end', { id: 'a', error: false }),
    event(6, 'plan', { todos: [{ content: 'Trace entry', status: 'completed' }] }),
  ].reduce(applyAgentEvent, emptyActivity());
  assert.equal(state.items[0].error, false);
  assert.equal(state.items[1].error, true);
  assert.equal(state.items[2].todos[0].status, 'in_progress');
  assert.equal(state.items[3].todos[0].status, 'completed');
});

test('old unkeyed events and compact replay remain readable without duplicated final answer', () => {
  const events = [
    event(1, 'draft', { content: 'First step' }),
    event(4, 'tool_start', { id: 'a', name: 'read_source' }),
    event(5, 'tool_end', { id: 'a' }),
    event(6, 'draft', { content: 'Final answer' }),
    event(9, 'text', { content: 'Final answer' }),
  ];
  const state = replayActivity({ events, cursor: 10 });
  assert.equal(state.items.length, 3);
  assert.equal(state.items[0].content, 'First step');
  assert.equal(state.items[2].final, true);
  assert.equal(state.cursor, 10);
  assert.equal(applyAgentEvent(state, event(8, 'draft', { content: 'Final' })), state);
});

test('interruptions preserve partial text; old tools do not restart on resume', () => {
  const state = [
    event(1, 'draft', { id: 'a', content: 'Checking source' }),
    event(2, 'tool_start', { id: 'read', name: 'read_source' }),
    event(3, 'status', { status: 'interrupted' }),
    event(4, 'status', { status: 'running' }),
    event(5, 'draft', { id: 'b', content: 'Continuing' }),
  ].reduce(applyAgentEvent, emptyActivity());
  assert.equal(state.items[0].content, 'Checking source');
  assert.equal(state.items[1].interrupted, true);
  assert.equal(state.items.at(-1).content, 'Continuing');
});

test('DSML-only empty message does not erase an earlier round', () => {
  const state = [
    event(1, 'draft', { id: 'a', content: 'Searching the selected repos.' }),
    event(2, 'draft', { id: 'b', content: '', done: true }),
    event(3, 'tool_start', { id: 's', name: 'search_source' }),
  ].reduce(applyAgentEvent, emptyActivity());
  assert.equal(state.items[0].content, 'Searching the selected repos.');
});
