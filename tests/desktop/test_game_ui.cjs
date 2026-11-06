const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.resolve(__dirname, '../../desktop/renderer/app.js'), 'utf8');
function setup() {
  const nodes = new Map();
  const timers = new Map();
  let nextTimer = 0;
  const context = {
    document: {
      hidden: false,
      querySelector: (selector) => {
        if (!nodes.has(selector)) nodes.set(selector, {});
        return nodes.get(selector);
      }
    },
    setInterval: (callback) => { timers.set(++nextTimer, callback); return nextTimer; },
    clearInterval: (id) => timers.delete(id)
  };
  vm.runInNewContext(source.replace(/const app = new DeskpetApp\(\);\s*app.init\(\);/, 'globalThis.App = DeskpetApp;'), context);
  const app = Object.create(context.App.prototype);
  Object.assign(app, {
    machine: { state: 'game' },
    gameStarting: false, gamePaused: false, gameTimer: null,
    drawGame() {},
    api: { post: async () => ({ session_id: 'new' }) }
  });
  return { app, nodes, timers };
}
function game(overrides = {}) {
  return { sessionId: 'one', snake: [{ x: 6, y: 6 }], food: { x: 9, y: 6 },
    direction: { x: 1, y: 0 }, steps: 0, score: 0, ended: false, reviewed: false,
    strategy: 'greedy_food', ...overrides };
}
function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test('start requests local rules and ignores duplicate clicks while pending', async () => {
  const { app, nodes, timers } = setup();
  const request = deferred();
  let calls = 0;
  app.api.post = (route, body) => {
    calls++;
    assert.equal(body.brain_level, 1);
    return request.promise;
  };
  const start = app.startGame();
  await app.startGame();
  assert.equal(calls, 1);
  assert.equal(nodes.get('#startGame').disabled, true);
  request.resolve({ session_id: 'new' });
  await start;
  assert.equal(nodes.get('#startGame').disabled, false);
  assert.equal(timers.size, 1);
});

test('food never overlaps snake across an entire deterministic game', () => {
  const { app } = setup();
  app.game = game();
  app.finishGame = () => { app.game.ended = true; };
  for (let i = 0; i < 120 && !app.game.ended; i++) {
    app.stepGame();
    assert.equal(app.game.snake.some((part) => part.x === app.game.food.x && part.y === app.game.food.y), false);
  }
  assert.ok(app.game.score > 0);
});

test('moving into the vacating tail is legal and walls are avoided', () => {
  const { app } = setup();
  app.game = game({
    snake: [[1,1],[2,1],[2,2],[1,2],[0,2],[0,1],[0,0],[1,0]].map(([x,y]) => ({ x,y })),
    food: { x: 5, y: 0 }
  });
  app.stepGame();
  assert.equal(app.game.snake[0].x, 1);
  assert.equal(app.game.snake[0].y, 0);
  app.game = game({ snake: [{ x: 11, y: 6 }], food: { x: 11, y: 11 } });
  app.stepGame();
  assert.equal(app.game.snake[0].x, 11);
  assert.equal(app.game.snake[0].y, 7);
});

test('ended game cannot restart during review; old review cannot replace new game UI', async () => {
  const { app, nodes, timers } = setup();
  const review = deferred();
  app.api.post = () => review.promise;
  app.game = game();
  const finishing = app.finishGame('collision');
  assert.equal(app.game.ended, true);
  app.toggleGamePause(true);
  app.toggleGamePause(false);
  assert.equal(timers.size, 0);
  app.game = game({ sessionId: 'two' });
  nodes.get('#gameLog').textContent = 'new game';
  review.resolve({ data: { takeaway: 'old review' } });
  await finishing;
  assert.equal(nodes.get('#gameLog').textContent, 'new game');
});

test('hidden game tab stays paused when the document becomes visible', () => {
  const { app, timers } = setup();
  app.game = game();
  app.machine.state = 'notice';
  app.toggleGamePause(false);
  assert.equal(app.gamePaused, true);
  assert.equal(timers.size, 0);
});
