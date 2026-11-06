const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.resolve(__dirname, '../../apps/deskpet/desktop/renderer/pet.js'), 'utf8');

function setup() {
  const listeners = new Map();
  const calls = [];
  let captured = null;
  const canvas = {
    setPointerCapture: (id) => { captured = id; },
    hasPointerCapture: (id) => captured === id,
    releasePointerCapture: () => { captured = null; },
    width: 2,
    height: 2,
    dataset: {},
    getContext: () => ({ clearRect: () => {}, drawImage: (...args) => calls.push(args), save() {}, restore() {}, translate() {}, scale() {} }),
    addEventListener: (name, callback) => listeners.set(name, callback),
    getBoundingClientRect: () => ({ x: 10, y: 20, width: 2, height: 2 })
  };
  const context = {
    window: { addEventListener: (name, callback) => listeners.set(name, callback) },
    document: {
      hidden: false,
      addEventListener: (name, callback) => listeners.set(name, callback),
      elementFromPoint: () => null
    },
    ResizeObserver: class { observe() {} },
    performance: { now: () => 0 },
    requestAnimationFrame: () => {}
  };
  vm.runInNewContext(source, context);
  const shell = {
    onCursor: (callback) => listeners.set('cursor', callback),
    setPetBounds: () => {},
    setMousePassthrough: (value) => calls.push(['ignore', value]),
    dragStart: (id) => calls.push(['start', id]),
    dragMove: (id) => calls.push(['move', id]),
    dragEnd: (id) => calls.push(['end', id])
  };
  const pet = new context.window.PetController(canvas, shell, () => calls.push(['click']));
  pet.mask = new Uint8ClampedArray(16);
  pet.mask[3] = 255;
  for (const key of ['idle', 'run', 'wave']) {
    pet.animations.set(key, {
      key, fps: 10, loop: true, ox: 0,
      frames: [{ name: 'a', oy: 0, w: 2, h: 2 }, { name: 'b', oy: 0, w: 2, h: 2 }]
    });
  }
  pet.images.set('a', {});
  pet.images.set('b', {});
  return { pet, calls, listeners };
}

test('native cursor polling restores opaque hit testing without DOM mouse events', () => {
  const { pet, calls } = setup();
  pet.cursor({ x: 0, y: 0 });
  pet.cursor({ x: 10, y: 20 });
  pet.cursor({ x: 10, y: 20 });
  assert.deepEqual(calls, [['ignore', true], ['ignore', false]]);
});


test('native dragging has no JavaScript pointer state or movement handlers', () => {
  const { pet, listeners } = setup();
  assert.equal(pet.drag, undefined);
  for (const event of ['pointerdown', 'pointermove', 'pointerup', 'lostpointercapture']) {
    assert.equal(listeners.has(event), false);
  }
  const css = fs.readFileSync(path.resolve(__dirname, '../../apps/deskpet/desktop/renderer/styles.css'), 'utf8');
  assert.match(css, /body\.desktop \.pet-canvas\s*\{[^}]*app-region: drag/);
});

test('run, walk and stroll stay disabled', () => {
  const { pet } = setup();
  pet.switchTo('idle');
  for (const name of ['run', 'walk', 'stroll']) {
    assert.equal(pet.switchTo(name, true), false);
    assert.equal(pet.canvas.dataset.action, 'idle');
  }
});
