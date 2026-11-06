const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const { EventEmitter } = require('node:events');
const source = fs.readFileSync(path.resolve(__dirname, '../../desktop/native-click.js'), 'utf8');

function setup() {
  const hooks = new Map();
  const events = new Map();
  let cursor = { x: 100, y: 100 };
  let position = [20, 30];
  let opened = 0;
  const releases = new EventEmitter();
  releases.close = () => {};
  const context = {
    module: { exports: {} }, __dirname: '', console, setImmediate,
    require: (name) => {
      if (name === 'node:child_process') return { spawn: () => Object.assign(new EventEmitter(), {
        stdout: {}, stderr: new EventEmitter(),
        stdin: Object.assign(new EventEmitter(), { write() {}, destroyed: false }),
        kill() {}
      }) };
      if (name === 'node:readline') return { createInterface: () => releases };
      return require(name);
    }
  };
  vm.runInNewContext(source, context);
  const { installNativeClick } = context.module.exports;
  const win = {
    webContents: { executeJavaScript: () => Promise.resolve(true) },
    hookWindowMessage: (id, callback) => hooks.set(id, callback),
    on: (id, callback) => events.set(id, callback),
    getPosition: () => position,
    isVisible: () => true,
    isDestroyed: () => false
  };
  installNativeClick(win, { getCursorScreenPoint: () => cursor }, () => { opened++; });
  const caption = Buffer.alloc(8);
  caption.writeUInt32LE(0x0201);
  return {
    hooks, events, releases,
    down: () => hooks.get(0x0210)(caption),
    move: (x, y) => { cursor = { x, y }; hooks.get(0x0216)(); },
    windowMove: (x, y) => { position = [x, y]; events.get('will-move')({}, { x, y }); },
    opened: () => opened
  };
}
const flush = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };

test('stationary click opens once even with both release messages', async () => {
  const state = setup();
  state.down();
  state.releases.emit('line', '1');
  state.hooks.get(0x0232)();
  await flush();
  assert.equal(state.opened(), 1);
});

test('50 alternating clicks and return-trip drags do not leak state', async () => {
  const state = setup();
  for (let i = 0; i < 50; i++) {
    state.down();
    state.move(130, 100);
    state.move(100, 100);
    state.hooks.get(0x0232)();
    state.down();
    state.hooks.get(0x0232)();
    await flush();
  }
  await flush();
  assert.equal(state.opened(), 50);
});

test('movement is latched even when cursor samples are unchanged', async () => {
  const state = setup();
  state.down();
  state.windowMove(50, 30);
  state.windowMove(20, 30);
  state.hooks.get(0x0232)();
  await flush();
  assert.equal(state.opened(), 0);
});

for (const cancel of ['blur', 'hide', 'closed', 0x001F]) {
  test('cancellation suppresses click: ' + cancel, async () => {
    const state = setup();
    state.down();
    (typeof cancel === 'number' ? state.hooks : state.events).get(cancel)();
    state.hooks.get(0x0232)();
    await flush();
    assert.equal(state.opened(), 0);
  });
}
test('focus loss before deferred open cancels the pending click', async () => {
  const state = setup();
  state.down();
  state.hooks.get(0x0232)();
  state.events.get('blur')();
  await flush();
  assert.equal(state.opened(), 0);
});
