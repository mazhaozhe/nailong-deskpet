'use strict';
const { spawn } = require('node:child_process');
const path = require('node:path');
const { createInterface } = require('node:readline');

function installNativeClick(win, screen, open, python, mouseScript) {
  let press = null;
  let generation = 0;
  // 会话 5：把「往上拖」也变成一条动作命令（结构化对象，渲染层按 type 分发）。
  // 判据：窗口相对按下点位移 ≥6px 且方向朝上（dy < 0）才算被提起来；
  // 单击（未移动）仍然只走 open()，不会被误判成拖拽。
  const LIFT_THRESHOLD = 6;
  function send(command) {
    if (win.isDestroyed()) return;
    win.webContents.send('pet:command', command);
  }
  // mouseScript 由主进程给：打包态必须在 asar 之外（Python 读不到 asar 内的文件），
  // 开发态就是 __dirname 下这一份。解释器同理——打包态是包内 runtime，不是系统 Python。
  const scriptPath = mouseScript || path.join(__dirname, 'native_mouse.py');
  const watcher = spawn(python, ['-u', scriptPath], {
    windowsHide: true,
    stdio: ['pipe', 'pipe', 'pipe']
  });
  const lines = createInterface({ input: watcher.stdout });
  watcher.on('error', (error) => console.error('[native-click] release monitor:', error.message));
  watcher.stdin.on('error', () => {});
  watcher.stderr.on('data', (data) => console.error('[native-click]', data.toString()));

  function sample(position = win.getPosition()) {
    if (!press) return;
    const cursor = screen.getCursorScreenPoint();
    const movedByCursor = Math.hypot(cursor.x - press.cursor.x, cursor.y - press.cursor.y) >= LIFT_THRESHOLD;
    const dx = position[0] - press.position[0];
    const dy = position[1] - press.position[1];
    if (movedByCursor || Math.hypot(dx, dy) >= LIFT_THRESHOLD) {
      press.moved = true;
    }
    if (!press.moved) return;
    // 朝上拖：dy 为负。已经在「被提起」状态就持续更新位移，否则只发一次 begin。
    if (dy <= -LIFT_THRESHOLD) {
      if (!press.lifted) {
        press.lifted = true;
        send({ type: 'lift', phase: 'begin', dy });
      } else {
        send({ type: 'lift', phase: 'move', dy });
      }
    }
  }

  function finish(id = generation) {
    if (!press || id !== generation) return;
    sample();
    const click = !press.moved;
    const lifted = press.lifted;
    const hit = press.hit;
    const ticket = generation;
    press = null;
    // 松手先落地（渲染层播 land 再回 idle），再决定是不是单击开面板
    if (lifted) send({ type: 'lift', phase: 'end', dy: 0 });
    if (click) hit.then((onPet) => {
      if (!onPet) return;
      setImmediate(() => {
        if (ticket === generation && !win.isDestroyed() && win.isVisible()) open();
      });
    });
  }

  function begin() {
    generation++;
    const cursor = screen.getCursorScreenPoint();
    const position = win.getPosition();
    const x = cursor.x - position[0];
    const y = cursor.y - position[1];
    const hit = win.webContents.executeJavaScript(
      `Boolean(document.elementFromPoint(${x}, ${y})?.closest('#petCanvas'))`
    ).catch(() => false);
    press = {
      cursor,
      position,
      hit,
      moved: false,
      lifted: false
    };
    if (!watcher.stdin.destroyed) watcher.stdin.write(generation + '\n');
  }

  // Chromium child windows report button presses through WM_PARENTNOTIFY.
  win.hookWindowMessage(0x0210, (wParam) => {
    if ((wParam.readUInt32LE(0) & 0xFFFF) === 0x0201) begin();
  });
  win.hookWindowMessage(0x00A1, (wParam) => {
    if (wParam.readUInt32LE(0) === 2) begin();
  });
  lines.on('line', (line) => finish(Number(line)));
  win.hookWindowMessage(0x0232, () => finish());
  win.hookWindowMessage(0x0216, () => sample());
  win.on('will-move', (_event, bounds) => sample([bounds.x, bounds.y]));
  const cancel = () => {
    // 被 blur / hide / 取消打断时也要告诉渲染层「这次拖拽结束了」，
    // 否则桌宠会永远保持「被提起来」的姿态（快速甩出窗口的典型现场）。
    if (press && press.lifted) send({ type: 'lift', phase: 'end', dy: 0 });
    press = null;
    generation++;
  };
  win.hookWindowMessage(0x001F, cancel);
  win.on('blur', cancel);
  win.on('hide', cancel);
  const dispose = () => {
    cancel();
    lines.close();
    watcher.kill();
  };
  win.on('closed', dispose);
  return dispose;
}

module.exports = { installNativeClick };
