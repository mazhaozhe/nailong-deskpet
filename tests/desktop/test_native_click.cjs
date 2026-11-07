const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const { EventEmitter } = require('node:events');
const source = fs.readFileSync(path.resolve(__dirname, '../../apps/deskpet/desktop/native-click.js'), 'utf8');

function setup() {
  const hooks = new Map();
  const events = new Map();
  const commands = [];
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
    webContents: {
      executeJavaScript: () => Promise.resolve(true),
      send: (channel, command) => { commands.push({ channel, command }); }
    },
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
    hooks, events, releases, commands,
    down: () => hooks.get(0x0210)(caption),
    move: (x, y) => { cursor = { x, y }; hooks.get(0x0216)(); },
    windowMove: (x, y) => { position = [x, y]; events.get('will-move')({}, { x, y }); },
    lifts: () => commands.filter((item) => item.command.type === 'lift').map((item) => item.command),
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

// ---- 会话 5：往上拉（lift）判定 + 快速甩动不丢状态 ----

test('lift: 窗口上移 6px 发 begin，继续上移发 move（不重复 begin）', async () => {
  const state = setup();
  state.down();
  state.windowMove(20, 22);          // dy = -8
  state.windowMove(20, 14);          // dy = -16
  state.windowMove(20, 6);           // dy = -24
  const emitted = state.lifts();
  assert.deepEqual(emitted.map((item) => item.phase), ['begin', 'move', 'move']);
  assert.ok(emitted[0].dy <= -6, 'begin 的 dy 必须真的朝上');
});

test('lift: 松手发 end，且这一下不再当单击开面板', async () => {
  const state = setup();
  state.down();
  state.windowMove(20, 10);          // dy = -20
  state.hooks.get(0x0232)();          // WM_EXITSIZEMOVE（finish 里会先 sample 一次，故可能夹一条 move）
  await flush();
  const phases = state.lifts().map((item) => item.phase);
  assert.equal(phases.filter((p) => p === 'begin').length, 1, 'begin 只许一次：' + phases);
  assert.equal(phases.filter((p) => p === 'end').length, 1, 'end 只许一次：' + phases);
  assert.equal(phases[phases.length - 1], 'end', '最后一条必须是 end：' + phases);
  assert.equal(state.opened(), 0, '被提起来再松手不该算单击');
});

test('lift: 朝下 / 横向拖动不触发 lift（方向判据）', async () => {
  const state = setup();
  state.down();
  state.windowMove(20, 60);          // 往下 dy = +30
  state.windowMove(80, 30);          // 往右
  state.move(200, 100);              // 光标右移
  state.hooks.get(0x0232)();
  await flush();
  assert.deepEqual(state.lifts(), [], '非朝上位移不许发 lift');
});

test('lift: 快速甩出窗口（blur/hide）必须补发 end，不把渲染层留在 lifted', async () => {
  for (const cancel of ['blur', 'hide']) {
    const state = setup();
    state.down();
    state.windowMove(20, 0);         // dy = -30，已经 begin
    assert.deepEqual(state.lifts().map((item) => item.phase), ['begin']);
    state.events.get(cancel)();      // 甩出去 / 失焦
    const phases = state.lifts().map((item) => item.phase);
    assert.deepEqual(phases, ['begin', 'end'], cancel + ' 之后没补 end，会卡在 lifted');
    // 之后即使再来一次松手消息，也不该重复发 end
    state.hooks.get(0x0232)();
    await flush();
    assert.deepEqual(state.lifts().map((item) => item.phase), ['begin', 'end']);
  }
});

test('lift: 连续 20 次提起-松手不泄漏状态', async () => {
  const state = setup();
  for (let i = 0; i < 20; i++) {
    state.down();
    state.windowMove(20, 10 - i);
    state.hooks.get(0x0232)();
    state.windowMove(20, 30);
    await flush();
  }
  const phases = state.lifts().map((item) => item.phase);
  assert.equal(phases.filter((p) => p === 'begin').length, 20);
  assert.equal(phases.filter((p) => p === 'end').length, 20);
  assert.equal(state.opened(), 0);
});
