const fs = require('node:fs');
const assert = require('node:assert/strict');
const { test } = require('node:test');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => {
  module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, filename);
};
const { createScrollFollower } = require('../../src/utils/chatScroll.ts');

function setup(t) {
  const frames = new Map();
  let frameId = 0;
  let resize;
  const changes = [];
  class Element extends EventTarget {
    scrollTop = 0;
    scrollHeight = 1000;
    clientHeight = 200;
    parentElement = null;
    overflowY = 'auto';
    reveals = 0;
    scrollTo({ top }) { this.scrollTop = Math.max(0, Math.min(top, this.scrollHeight - this.clientHeight)); }
    scrollIntoView() { this.reveals++; }
    querySelector() { return end; }
    closest() { return this.input ? this : null; }
  }
  const restore = {};
  const globals = {
    Element,
    ResizeObserver: class { constructor(callback) { resize = callback; } observe() {} disconnect() {} },
    requestAnimationFrame: callback => { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame: id => frames.delete(id),
    getComputedStyle: element => ({ overflowY: element.overflowY }),
  };
  for (const [key, value] of Object.entries(globals)) { restore[key] = global[key]; global[key] = value; }
  const view = new Element();
  const content = new Element();
  const end = new Element();
  const follower = createScrollFollower(view, content, value => changes.push(value));
  t.after(() => { follower.dispose(); for (const key of Object.keys(globals)) global[key] = restore[key]; });
  const flush = () => { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback()); };
  const emit = (type, props = {}, target = view) => {
    const event = new Event(type);
    Object.assign(event, props);
    Object.defineProperty(event, 'target', { value: target });
    view.dispatchEvent(event);
  };
  flush();
  return { view, content, end, follower, changes, flush, emit, resize: () => resize(), Element };
}

test('layout scroll events do not disable following before ResizeObserver runs', t => {
  const s = setup(t);
  s.view.scrollHeight = 1300;
  s.emit('scroll');
  s.resize(); s.flush();
  assert.equal(s.view.scrollTop, 1100);
  assert.deepEqual(s.changes, []);
  s.view.clientHeight = 100;
  s.emit('scroll'); s.resize(); s.flush();
  assert.equal(s.view.scrollTop, 1200);
});

test('upward user intent cancels an already queued automatic scroll', t => {
  const s = setup(t);
  s.view.scrollHeight = 1200; s.resize();
  s.emit('wheel', { deltaY: -100, ctrlKey: false });
  s.view.scrollTop = 600; s.emit('scroll'); s.flush();
  assert.equal(s.view.scrollTop, 600);
  assert.deepEqual(s.changes, [false]);
});

test('near bottom is not bottom; reattach only after a genuine bottom scroll', t => {
  const s = setup(t);
  s.view.scrollTop = 765; s.emit('scroll');
  assert.deepEqual(s.changes, [false]);
  s.view.scrollHeight = 1200; s.resize(); s.flush();
  assert.equal(s.view.scrollTop, 765);
  s.view.scrollTop = 1000; s.emit('scroll');
  assert.deepEqual(s.changes, [false, true]);
  s.view.scrollHeight = 1400; s.resize(); s.flush();
  assert.equal(s.view.scrollTop, 1200);
});

test('jump reaches the real maximum and keeps following delayed reflow', t => {
  const s = setup(t);
  s.view.scrollTop = 100; s.emit('scroll');
  s.follower.jump();
  assert.equal(s.end.reveals, 1);
  assert.equal(s.view.scrollTop, 800);
  s.view.scrollHeight = 1500; s.resize(); s.flush();
  assert.equal(s.view.scrollTop, 1300);
  s.follower.jump(false);
  assert.equal(s.end.reveals, 1, 'session reset must not scroll ancestor viewports');
});

test('nested code scrolling and zoom gestures do not detach the outer transcript', t => {
  const s = setup(t);
  const child = new s.Element(); child.parentElement = s.view; child.scrollTop = 100;
  s.emit('wheel', { deltaY: -50, ctrlKey: false }, child);
  s.emit('wheel', { deltaY: -50, ctrlKey: true });
  s.view.scrollHeight = 1200; s.resize(); s.flush();
  assert.equal(s.view.scrollTop, 1000);
  assert.deepEqual(s.changes, []);
});

test('keyboard, touch and scrollbar intent stop pending follow', t => {
  const s = setup(t);
  for (const action of [
    () => s.emit('keydown', { key: 'PageUp' }),
    () => { s.emit('touchstart', { touches: [{ clientY: 100 }] }); s.emit('touchmove', { touches: [{ clientY: 200 }] }); },
    () => s.emit('pointerdown'),
  ]) {
    s.follower.jump(false); s.flush();
    const top = s.view.scrollTop;
    s.view.scrollHeight += 100; s.resize(); action(); s.flush();
    assert.equal(s.view.scrollTop, top);
    assert.equal(s.changes.at(-1), false);
  }
});

test('dispose removes listeners and pending work', t => {
  const s = setup(t);
  s.view.scrollHeight = 1200; s.resize();
  s.follower.dispose(); s.flush();
  s.emit('wheel', { deltaY: -20 });
  assert.equal(s.view.scrollTop, 800);
  assert.deepEqual(s.changes, []);
});
