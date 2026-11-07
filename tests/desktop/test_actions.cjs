// 会话 5：动作调度层（renderer/actions.js）的单元测试。
// 取代原来那份桌宠 UI 单测（小游戏已下架），覆盖：
//   1. 动作键表可枚举且 ≥8 态，优先级单调、anim 都在 manifest 里真实存在；
//   2. 优先级冲突 ≥3 组（思考中收到 mood / 被提起时来任何请求 / 高优先级抢占低优先级）；
//   3. ttl 到期回落 idle，release(source) 精确释放；
//   4. 同键续期不重置帧（不重复 switchTo，避免视觉抖动）。
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const RENDERER = path.resolve(__dirname, '../../apps/deskpet/desktop/renderer');
const source = fs.readFileSync(path.join(RENDERER, 'actions.js'), 'utf8');
const manifest = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../../apps/deskpet/desktop/assets/nailong/manifest.json'), 'utf8')
);

function setup() {
  let clock = 0;
  const context = { window: {}, performance: { now: () => clock } };
  vm.runInNewContext(source, context);
  const pet = {
    calls: [],
    holds: [],
    switchTo(key) { this.calls.push(key); return true; },
    setHold(value) { this.holds.push(value); }
  };
  const scene = { dataset: {} };
  const Director = context.window.ActionDirector;
  const director = new Director({ pet, scene, now: () => clock });
  const advance = (ms) => { clock += ms; };
  return { director, pet, scene, advance, Director };
}

test('动作键表至少 8 态，优先级单调，且全部 anim 都在 manifest 里', (t) => {
  const { director } = setup();
  const table = director.describe();
  assert.ok(table.length >= 8, `动作键只有 ${table.length} 个`);
  const keys = table.map((item) => item.key);
  for (const required of ['idle', 'thinking', 'lifted', 'land', 'work', 'celebrate', 'wave', 'sleep', 'shy']) {
    assert.ok(keys.includes(required), `缺少动作键 ${required}`);
  }
  const anims = new Set(manifest.animations.map((item) => item.key));
  for (const item of table) {
    assert.ok(anims.has(item.anim), `${item.key} 指向的动画 ${item.anim} 不在 manifest 里`);
    assert.equal(typeof item.ttl, 'number');
  }
  const byKey = Object.fromEntries(table.map((item) => [item.key, item.priority]));
  assert.ok(byKey.lifted === Math.max(...table.map((i) => i.priority)), 'lifted 必须是最高优先级');
  assert.ok(byKey.idle === 0, 'idle 必须是最低优先级');
  assert.ok(byKey.lifted > byKey.thinking && byKey.thinking > byKey.work, '优先级链断裂');
  assert.ok(byKey.work > byKey.celebrate && byKey.celebrate > byKey.wave, '优先级链断裂');
  assert.ok(byKey.wave > byKey.shy, '优先级链断裂');
});

test('冲突一：思考态占用时 mood 请求进队列，不抢帧', () => {
  const { director, pet } = setup();
  assert.equal(director.play('thinking', { source: 'chat' }), true);
  pet.calls.length = 0;
  assert.equal(director.play('shy', { priority: director.constructor.PRIORITY.MOOD, source: 'chat-mood' }), false);
  assert.equal(director.current(), 'thinking');
  assert.deepEqual(pet.calls, [], '低优先级请求不许触碰渲染层');
  // 思考结束 → 排队的那条接上
  director.release('chat');
  assert.equal(director.current(), 'shy');
});

test('冲突二：被提起来期间任何请求都进队列，lifted 不被顶掉', () => {
  const { director } = setup();
  director.play('lifted', { source: 'drag' });
  assert.equal(director.play('thinking', { source: 'chat' }), false);
  assert.equal(director.play('celebrate', { source: 'doc' }), false);
  assert.equal(director.current(), 'lifted');
  // 松手：同源接管（land 顶掉自己占着的 lifted），落地后回落到排队里优先级最高的 celebrate
  assert.equal(director.play('land', { ttl: 300, source: 'drag' }), true);
  assert.equal(director.current(), 'land');
  director.tick(1000);
  // 队列只留优先级最高的那一条：thinking(60) 挤掉了 celebrate(40)
  assert.equal(director.current(), 'thinking', '落地后应接上队列里优先级最高的动作');
});

test('冲突三：高优先级当场抢占低优先级（干活中要思考）', () => {
  const { director, pet } = setup();
  director.play('work', { source: 'export' });
  assert.equal(director.current(), 'work');
  pet.calls.length = 0;
  assert.equal(director.play('thinking', { source: 'chat' }), true);
  assert.equal(director.current(), 'thinking');
  assert.deepEqual(pet.calls, ['sit'], 'thinking 必须落到 sit 帧');
});

test('ttl 到期自动回落 idle，未到期不动', () => {
  const { director, pet, advance } = setup();
  director.play('work', { ttl: 1000, source: 'export' });
  advance(999);
  assert.equal(director.tick(), false);
  assert.equal(director.current(), 'work');
  advance(1);
  assert.equal(director.tick(), true);
  assert.equal(director.current(), 'idle');
  assert.equal(pet.calls[pet.calls.length - 1], 'idle');
  assert.equal(pet.holds[pet.holds.length - 1], false, '回 idle 时要交还控制权');
});

test('release 只放掉自己来源的动作，不误伤别的来源', () => {
  const { director } = setup();
  director.play('thinking', { source: 'chat' });
  assert.equal(director.release('agent'), false);
  assert.equal(director.current(), 'thinking');
  assert.equal(director.release('chat'), true);
  assert.equal(director.current(), 'idle');
  assert.equal(director.current(), 'idle');
});

test('同一动作重复请求只续期，不重复切帧（防抖动）', () => {
  const { director, pet, advance } = setup();
  director.play('thinking', { ttl: 1000, source: 'chat' });
  pet.calls.length = 0;
  advance(500);
  director.play('thinking', { ttl: 4000, source: 'chat' });
  assert.deepEqual(pet.calls, [], '同键续期不该再调 switchTo');
  advance(3999);
  assert.equal(director.tick(), false, '续期后应按新 ttl 计算');
});

test('场景容器暴露 data-action / data-pose，验收可直接查 DOM', () => {
  const { director, scene } = setup();
  director.play('lifted', { source: 'drag' });
  assert.equal(scene.dataset.action, 'lifted');
  assert.equal(scene.dataset.pose, 'pose-lifted');
  director.reset();
  assert.equal(scene.dataset.action, 'idle');
  assert.equal(scene.dataset.pose, undefined);
});

test('未知动作键直接拒绝，不污染当前状态', () => {
  const { director } = setup();
  director.play('work', { source: 'export' });
  assert.equal(director.play('fly-to-moon'), false);
  assert.equal(director.current(), 'work');
});

test('历史记录保留最近切换，便于验收抽查', () => {
  const { director } = setup();
  director.play('thinking', { source: 'chat' });
  director.reset();
  const keys = [...director.history].map((item) => item.key);
  assert.deepEqual(keys.slice(-2), ['thinking', 'idle']);
  assert.ok(director.history.length <= 20);
});
