// Run with apps/deskpet/desktop/node_modules/electron/dist/electron.exe tests/desktop/test_pet_drag_electron.cjs.
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');

process.env.NAIWA_SMOKE_TEST = '1';
const entry = path.resolve(__dirname, '../../apps/deskpet/desktop/main.js');
const source = fs.readFileSync(entry, 'utf8').replace(
  'setTimeout(runSmokeTest, 1800)',
  'setTimeout(runDragWindowTest, 500)'
);
const runner = new Module(entry, module);
runner.filename = entry;
runner.paths = Module._nodeModulePaths(path.dirname(entry));
runner._compile(source + `
async function runDragWindowTest() {
  const assert = require('node:assert/strict');
  try {
    for (let wait = 0; wait < 100; wait++) {
      const ready = await mainWindow.webContents.executeJavaScript(
        "Number(document.querySelector('#petCanvas')?.dataset.frames) === 74"
      );
      if (ready) break;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    const state = await mainWindow.webContents.executeJavaScript(\`({
      dragRegion: getComputedStyle(document.querySelector('#petCanvas')).getPropertyValue('-webkit-app-region'),
      buttonRegion: getComputedStyle(document.querySelector('#bubble')).getPropertyValue('-webkit-app-region'),
      frames: Number(document.querySelector('#petCanvas').dataset.frames)
    })\`);
    assert.equal(state.dragRegion, 'drag');
    assert.equal(state.buttonRegion, 'no-drag');
    assert.equal(state.frames, 74);
    assert.equal(await mainWindow.webContents.executeJavaScript("document.querySelector('#openPanel') === null"), true);
    mainWindow.webContents.send('pet:command', 'open');
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(await mainWindow.webContents.executeJavaScript("document.querySelector('#panel').hidden"), false);
    const game = await mainWindow.webContents.executeJavaScript(\`(async () => {
      let brainLevel = null;
      const originalFetch = window.fetch;
      window.fetch = (url, options) => {
        if (String(url).endsWith('/game/start')) brainLevel = JSON.parse(options.body).brain_level;
        return originalFetch(url, options);
      };
      try {
        document.querySelector('[data-view="game"]').click();
        const started = performance.now();
        document.querySelector('#startGame').click();
        for (let i = 0; i < 100 && document.querySelector('#finishGame').disabled; i++) {
          await new Promise(resolve => setTimeout(resolve, 20));
        }
        return {
          brainLevel,
          started: !document.querySelector('#finishGame').disabled,
          elapsedMs: Math.round(performance.now() - started),
          log: document.querySelector('#gameLog').textContent
        };
      } finally {
        window.fetch = originalFetch;
      }
    })()\`);
    assert.equal(game.brainLevel, 1);
    assert.equal(game.started, true);
    log('GAME_START_TEST ' + JSON.stringify(game));
    log('NATIVE_DRAG_SETUP_TEST pass=true region=drag customMovement=false panelCommand=true bubbleButtonRemoved=true');
  } catch (error) {
    log('NATIVE_DRAG_SETUP_TEST pass=false ' + error.message);
  } finally {
    isQuitting = true;
    app.quit();
  }
}
`, entry);
