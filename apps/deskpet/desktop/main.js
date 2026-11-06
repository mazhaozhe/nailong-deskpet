const { app, BrowserWindow, Tray, Menu, Notification, nativeImage, ipcMain, safeStorage, screen, shell, dialog } = require('electron');
const { spawn } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const net = require('net');
const path = require('path');
const { installNativeClick } = require('./native-click');

const ROOT = path.resolve(__dirname, '../../..');
const PYTHON = path.join(ROOT, '.venv', 'Scripts', 'python.exe');
const WINDOW_SIZE = { width: 430, height: 760 };
const SMOKE_TEST = process.env.NAIWA_SMOKE_TEST === '1';
const SMOKE_UID = '7f2d8882-76b9-4c7b-b45d-74dc38c8c073';

let mainWindow = null;
let tray = null;
let backend = null;
let apiPort = 0;
let currentUserId = SMOKE_UID;
let isQuitting = false;
let mouseIgnored = true;
let cursorTimer = null;
let disposeNativeClick = null;
let positionSaveTimer = null;
let notificationTimer = null;
let backendGeneration = 0;
let runtimeLog = '';
const notifiedTodos = new Set();

if (SMOKE_TEST) {
  app.setPath('userData', path.join(app.getPath('temp'), `deskpet-stage5-${process.pid}`));
}

const settingsFile = () => path.join(app.getPath('userData'), 'settings.json');

function readJson(file, fallback) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return fallback; }
}

function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temp = `${file}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(value, null, 2), 'utf8');
  fs.renameSync(temp, file);
}

function getSettings() {
  return readJson(settingsFile(), {});
}

function publicDesktopSettings() {
  const saved = getSettings();
  return {
    theme: saved.theme === 'night' ? 'night' : 'day',
    alwaysOnTop: saved.alwaysOnTop !== false,
    muted: Boolean(saved.muted),
    hasKey: Boolean(saved.encryptedKey)
  };
}

function updateDesktopSettings(patch) {
  const next = { ...getSettings(), ...patch };
  writeJson(settingsFile(), next);
  return publicDesktopSettings();
}

function log(message) {
  const line = String(message);
  runtimeLog = `${runtimeLog}${line}\n`.slice(-65536);
  console.log(line);
}

function decryptApiKey() {
  const encrypted = getSettings().encryptedKey;
  if (!encrypted || !safeStorage.isEncryptionAvailable()) return '';
  try { return safeStorage.decryptString(Buffer.from(encrypted, 'base64')); } catch { return ''; }
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

function apiRequest(method, route, body) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? null : JSON.stringify(body);
    const request = http.request({
      hostname: '127.0.0.1', port: apiPort, path: route, method,
      headers: {
        'X-User-Id': currentUserId,
        ...(payload ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } : {})
      },
      timeout: 10000
    }, (response) => {
      let text = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => { text += chunk; });
      response.on('end', () => {
        try {
          const data = text ? JSON.parse(text) : {};
          if (response.statusCode >= 400) reject(new Error(data.detail || `HTTP ${response.statusCode}`));
          else resolve(data);
        } catch { reject(new Error('本地服务返回了不可读取的响应')); }
      });
    });
    request.on('timeout', () => request.destroy(new Error('本地服务请求超时')));
    request.on('error', reject);
    if (payload) request.write(payload);
    request.end();
  });
}

async function waitForBackend(generation, attempts = 100) {
  for (let i = 0; i < attempts; i += 1) {
    if (generation !== backendGeneration) throw new Error('本地服务已重启');
    try {
      await apiRequest('GET', '/api/growth');
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  throw new Error('本地 FastAPI 启动超时');
}

async function stopBackend() {
  backendGeneration += 1;
  const child = backend;
  backend = null;
  if (!child || child.killed || child.exitCode !== null) return;
  await new Promise((resolve) => {
    const timer = setTimeout(() => {
      try { child.kill('SIGKILL'); } catch {}
      resolve();
    }, 2500);
    child.once('exit', () => { clearTimeout(timer); resolve(); });
    try { child.kill(); } catch { clearTimeout(timer); resolve(); }
  });
}

async function startBackend() {
  if (!fs.existsSync(PYTHON)) throw new Error('未找到项目 .venv，请先安装 Python 依赖');
  const generation = ++backendGeneration;
  apiPort = await getFreePort();
  const apiKey = decryptApiKey();
  const child = spawn(PYTHON, [
    '-m', 'uvicorn', 'apps.deskpet.backend.app:app',
    '--host', '127.0.0.1', '--port', String(apiPort), '--log-level', 'warning'
  ], {
    cwd: ROOT,
    env: {
      ...process.env,
      DEEPSEEK_API_KEY: apiKey,
      DESKPET_ACCESS_TOKEN: '',
      DESKPET_DATA_DIR: path.join(app.getPath('userData'), 'deskpet'),
      PYTHONPATH: ROOT,
      PYTHONUNBUFFERED: '1',
      PYTHONIOENCODING: 'utf-8'
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true
  });
  backend = child;
  const redact = (chunk) => {
    let text = chunk.toString();
    if (apiKey) text = text.split(apiKey).join('[redacted]');
    return text.trim();
  };
  child.stdout.on('data', (chunk) => { const text = redact(chunk); if (text) log(`[fastapi] ${text}`); });
  child.stderr.on('data', (chunk) => { const text = redact(chunk); if (text) log(`[fastapi] ${text}`); });
  child.on('exit', (code) => {
    if (backend === child && !isQuitting) log(`[fastapi] exited code=${code}`);
  });
  log(`[startup] ${JSON.stringify({ host: '127.0.0.1', port: apiPort, hasKey: Boolean(apiKey), backend: 'fastapi' })}`);
  await waitForBackend(generation);
}

async function restartBackend() {
  await stopBackend();
  await startBackend();
  if (mainWindow && !mainWindow.isDestroyed()) {
    await mainWindow.loadURL(`http://127.0.0.1:${apiPort}/?desktop=1${SMOKE_TEST ? '&smoke=1' : ''}`);
  }
}

function clampPosition(x, y, bounds = { x: 0, y: 0, ...WINDOW_SIZE }) {
  const area = screen.getDisplayNearestPoint({
    x: Math.round(x + bounds.x + bounds.width / 2),
    y: Math.round(y + bounds.y + bounds.height / 2)
  }).workArea;
  const left = area.x - bounds.x;
  const top = area.y - bounds.y;
  const right = area.x + area.width - bounds.x - bounds.width;
  const bottom = area.y + area.height - bounds.y - bounds.height;
  return {
    x: Math.round(Math.max(left, Math.min(x, right))),
    y: Math.round(Math.max(top, Math.min(y, bottom))),
    hitLeft: x <= left,
    hitRight: x >= right,
    hitTop: y <= top,
    hitBottom: y >= bottom
  };
}

function setMousePassthrough(ignore) {
  if (!mainWindow || mainWindow.isDestroyed() || mouseIgnored === ignore) return;
  mainWindow.setIgnoreMouseEvents(ignore, { forward: true });
  mouseIgnored = ignore;
}

function saveWindowPosition() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const [x, y] = mainWindow.getPosition();
  updateDesktopSettings({ position: { x, y } });
}

function showMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.show();
  mainWindow.focus();
  mainWindow.webContents.send('pet:command', 'open');
}

function notify(title, body) {
  try {
    if (!Notification.isSupported()) return false;
    const item = new Notification({ title, body: String(body || '').slice(0, 180), silent: publicDesktopSettings().muted });
    item.on('click', showMainWindow);
    item.show();
    return true;
  } catch { return false; }
}

function createTray() {
  const icon = nativeImage.createFromPath(path.join(__dirname, 'assets', 'nailong', 'frames', 'r00_c00.png')).resize({ width: 20, height: 20 });
  tray = new Tray(icon);
  tray.setToolTip('奶龙桌宠');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '打开', click: showMainWindow },
    { label: '退出', click: () => { isQuitting = true; app.quit(); } }
  ]));
  tray.on('double-click', showMainWindow);
}

function createWindow() {
  const saved = getSettings();
  const area = screen.getPrimaryDisplay().workArea;
  const fallback = { x: area.x + area.width - WINDOW_SIZE.width - 24, y: area.y + area.height - WINDOW_SIZE.height - 24 };
  const position = saved.position && Number.isFinite(saved.position.x) && Number.isFinite(saved.position.y) ? saved.position : fallback;
  const clamped = clampPosition(position.x, position.y);
  mainWindow = new BrowserWindow({
    width: WINDOW_SIZE.width,
    height: WINDOW_SIZE.height,
    minWidth: WINDOW_SIZE.width,
    maxWidth: WINDOW_SIZE.width,
    minHeight: WINDOW_SIZE.height,
    maxHeight: WINDOW_SIZE.height,
    x: clamped.x,
    y: clamped.y,
    show: false,
    transparent: true,
    alwaysOnTop: saved.alwaysOnTop !== false,
    frame: false,
    resizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    hasShadow: false,
    title: '奶龙桌宠',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });
  if (process.platform === 'win32') {
    disposeNativeClick = installNativeClick(mainWindow, screen, () => mainWindow.webContents.send('pet:command', 'open'), PYTHON);
  }
  mainWindow.setAlwaysOnTop(saved.alwaysOnTop !== false, 'floating');
  mainWindow.setIgnoreMouseEvents(true, { forward: true });
  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      saveWindowPosition();
      mainWindow.hide();
    }
  });
  mainWindow.on('moved', () => {
    clearTimeout(positionSaveTimer);
    positionSaveTimer = setTimeout(saveWindowPosition, 250);
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\//.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.once('ready-to-show', () => { if (!SMOKE_TEST) mainWindow.show(); });
  mainWindow.loadURL(`http://127.0.0.1:${apiPort}/?desktop=1${SMOKE_TEST ? '&smoke=1' : ''}`);
}

async function checkDueNotifications() {
  try {
    const result = await apiRequest('GET', '/api/catchup');
    const items = [...(result.data.overdue || []), ...(result.data.due_soon || [])];
    for (const todo of items) {
      if (notifiedTodos.has(todo.id)) continue;
      notifiedTodos.add(todo.id);
      notify('奶龙提醒：还差一步', `${todo.title}${todo.gap ? ` · 还差 ${todo.gap}` : ''}`);
    }
  } catch {}
}

function promptForApiKey() {
  return new Promise((resolve) => {
    const script = `
Add-Type -AssemblyName System.Windows.Forms
$form = New-Object System.Windows.Forms.Form
$form.Text = '配置 DeepSeek API Key'
$form.Width = 460; $form.Height = 190; $form.StartPosition = 'CenterScreen'; $form.TopMost = $true
$label = New-Object System.Windows.Forms.Label
$label.Text = 'Key 仅由桌面主进程接收并加密保存'; $label.Left = 18; $label.Top = 16; $label.Width = 400
$box = New-Object System.Windows.Forms.TextBox
$box.Left = 18; $box.Top = 48; $box.Width = 400; $box.UseSystemPasswordChar = $true
$ok = New-Object System.Windows.Forms.Button
$ok.Text = '保存'; $ok.Left = 240; $ok.Top = 90; $ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = '取消'; $cancel.Left = 335; $cancel.Top = 90; $cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.AddRange(@($label,$box,$ok,$cancel)); $form.AcceptButton = $ok; $form.CancelButton = $cancel
if ($form.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($box.Text.Trim()) }
$form.Dispose()
`;
    const child = spawn('powershell.exe', ['-NoProfile', '-STA', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')], {
      stdio: ['ignore', 'pipe', 'ignore'], windowsHide: true
    });
    let output = '';
    child.stdout.on('data', (chunk) => { output += chunk.toString(); });
    child.on('error', () => resolve(''));
    child.on('close', () => resolve(output.trim()));
  });
}

function showPetMenu() {
  const settings = publicDesktopSettings();
  Menu.buildFromTemplate([
    { label: '打开清单', click: () => mainWindow.webContents.send('pet:command', 'open') },
    { label: settings.alwaysOnTop ? '取消置顶' : '置顶', click: () => {
      const next = updateDesktopSettings({ alwaysOnTop: !settings.alwaysOnTop });
      mainWindow.setAlwaysOnTop(next.alwaysOnTop, 'floating');
      mainWindow.webContents.send('desktop:settings', next);
    } },
    { label: settings.muted ? '取消静音' : '静音', click: () => {
      const next = updateDesktopSettings({ muted: !settings.muted });
      mainWindow.webContents.send('desktop:settings', next);
    } },
    { type: 'separator' },
    { label: '退出', click: () => { isQuitting = true; app.quit(); } }
  ]).popup({ window: mainWindow });
}

async function runSmokeTest() {
  const evidence = { pass: false };
  try {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const ui = await mainWindow.webContents.executeJavaScript(`(async () => {
      const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const $ = (s) => document.querySelector(s);
      const $$ = (s) => [...document.querySelectorAll(s)];
      const initial = {
        panelCollapsed: $('#panel').hidden,
        keyPromptHidden: $('#keyPrompt').hidden,
        todoCount: $$('#todoList .todo-item').length,
        days: $('#daysBadge').textContent,
        animations: Number($('#petCanvas').dataset.animations),
        uniqueFrames: Number($('#petCanvas').dataset.frames),
        canvasVisible: $('#petCanvas').getBoundingClientRect().height > 0
      };
      $('#petCanvas').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
      const opened = !$('#panel').hidden;
      $('[data-sample]').click();
      const sampleOnlyFills = $('#noticeText').value.length > 0 && $$('#todoList .todo-item').length === 0;
      $('#parseNotice').click();
      for (let i = 0; i < 100 && !$('#checklist').children.length; i += 1) await wait(100);
      const parsed = {
        decisionStates: $('#decisionChain').children.length,
        checklistItems: $('#checklist').children.length,
        degraded: $('#bubble').classList.contains('degraded'),
        degradedText: $('#bubbleNote').textContent,
        keyPromptAfterParse: !$('#keyPrompt').hidden
      };
      $('#laterKey').click();
      $('#confirmChecklist').click();
      for (let i = 0; i < 80 && !$$('#todoList .todo-item').length; i += 1) await wait(100);
      $('#exportDocx').click();
      for (let i = 0; i < 80 && !$('#exportResult a'); i += 1) await wait(100);
      $('[data-simulate="30"]').click();
      await wait(400);
      const simulated = $('#daysBadge').textContent;
      return { initial, opened, sampleOnlyFills, parsed,
        todosAfterConfirm: $$('#todoList .todo-item').length,
        exportReady: Boolean($('#exportResult a')),
        simulated,
        commentaryDefaultOff: !$('#commentaryEnabled').checked,
        modelCalls: (await (await fetch('/api/debug/model-calls')).json()).total
      };
    })()`);
    Object.assign(evidence, ui);
    await mainWindow.reload();
    await new Promise((resolve) => setTimeout(resolve, 900));
    evidence.refreshRealDays = await mainWindow.webContents.executeJavaScript(`document.querySelector('#daysBadge').textContent`);
    evidence.refreshCollapsed = await mainWindow.webContents.executeJavaScript(`document.querySelector('#panel').hidden`);
    evidence.noKeyStatus = await apiRequest('GET', '/api/key/status');
    evidence.dynamicPort = apiPort !== 8000 && apiPort > 0;
    evidence.loopbackOnly = backend.spawnargs.includes('127.0.0.1') && !backend.spawnargs.includes('0.0.0.0');

    const secret = `sk-smoke-${crypto.randomBytes(18).toString('hex')}`;
    updateDesktopSettings({ encryptedKey: safeStorage.encryptString(secret).toString('base64') });
    await restartBackend();
    await new Promise((resolve) => setTimeout(resolve, 500));
    evidence.injectedKeyStatus = await apiRequest('GET', '/api/key/status');
    const renderedHtml = await mainWindow.webContents.executeJavaScript('document.documentElement.outerHTML');
    evidence.keyLeakChecks = {
      renderer: renderedHtml.includes(secret),
      commandLine: backend.spawnargs.join(' ').includes(secret),
      logs: runtimeLog.includes(secret)
    };
    evidence.injectedModelCalls = (await apiRequest('GET', '/api/debug/model-calls')).total;
    await apiRequest('DELETE', '/api/key');
    const afterDelete = getSettings();
    delete afterDelete.encryptedKey;
    writeJson(settingsFile(), afterDelete);
    evidence.deletedKeyStatus = await apiRequest('GET', '/api/key/status');
    evidence.publicAfterDelete = publicDesktopSettings();

    mainWindow.close();
    evidence.closeToTray = Boolean(mainWindow && !mainWindow.isDestroyed() && !mainWindow.isVisible() && tray);
    showMainWindow();
    evidence.trayReopen = mainWindow.isVisible();
    evidence.notificationShowReturned = notify('奶龙桌宠自检', '系统通知通道已调用');
    evidence.pass = Boolean(
      ui.initial.panelCollapsed && ui.initial.keyPromptHidden && ui.initial.todoCount === 0 &&
      ui.initial.animations === 13 && ui.initial.uniqueFrames === 74 && ui.initial.canvasVisible &&
      ui.opened && ui.sampleOnlyFills && ui.parsed.decisionStates >= 4 && ui.parsed.checklistItems > 0 &&
      ui.parsed.degraded && ui.parsed.degradedText.includes('规则版，未调模型') && ui.parsed.keyPromptAfterParse &&
      ui.todosAfterConfirm > 0 && ui.exportReady && ui.simulated.includes('30') &&
      ui.commentaryDefaultOff && ui.modelCalls === 0 && evidence.refreshRealDays.includes('第 1 天') &&
      evidence.refreshCollapsed && evidence.noKeyStatus.configured === false && evidence.dynamicPort &&
      evidence.loopbackOnly && evidence.injectedKeyStatus.configured === true &&
      evidence.injectedKeyStatus.masked_tail === secret.slice(-4) &&
      !evidence.keyLeakChecks.renderer && !evidence.keyLeakChecks.commandLine && !evidence.keyLeakChecks.logs &&
      evidence.injectedModelCalls === 0 && evidence.deletedKeyStatus.configured === false &&
      !evidence.publicAfterDelete.hasKey && evidence.closeToTray && evidence.trayReopen && evidence.notificationShowReturned
    );
  } catch (error) {
    evidence.error = error.message;
  }
  log(`DESKTOP_SMOKE ${JSON.stringify(evidence)}`);
  isQuitting = true;
  await stopBackend();
  app.exit(evidence.pass ? 0 : 1);
}

app.whenReady().then(async () => {
  await startBackend();
  createTray();
  createWindow();
  ipcMain.handle('runtime:get', () => ({ host: '127.0.0.1', port: apiPort }));
  ipcMain.handle('desktop:settings:get', () => publicDesktopSettings());
  ipcMain.on('user:identity', (_event, uid) => {
    if (typeof uid === 'string' && /^[0-9a-f-]{36}$/i.test(uid)) currentUserId = uid;
  });
  ipcMain.on('mouse:passthrough', (_event, ignore) => {
    if (mainWindow) {
      setMousePassthrough(Boolean(ignore));
    }
  });
  cursorTimer = setInterval(() => {
    if (!mainWindow || mainWindow.isDestroyed() || !mainWindow.isVisible()) return;
    const cursor = screen.getCursorScreenPoint();
    const [x, y] = mainWindow.getPosition();
    mainWindow.webContents.send('pet:cursor', { x: cursor.x - x, y: cursor.y - y });
  }, 50);
  ipcMain.on('pet:context-menu', showPetMenu);
  ipcMain.on('desktop:top', (_event, value) => {
    const next = updateDesktopSettings({ alwaysOnTop: Boolean(value) });
    mainWindow.setAlwaysOnTop(next.alwaysOnTop, 'floating');
  });
  ipcMain.on('desktop:mute', (_event, value) => updateDesktopSettings({ muted: Boolean(value) }));
  ipcMain.on('desktop:theme', (_event, value) => updateDesktopSettings({ theme: value === 'night' ? 'night' : 'day' }));
  ipcMain.handle('key:change', async () => {
    const key = await promptForApiKey();
    if (!key) return publicDesktopSettings();
    if (!safeStorage.isEncryptionAvailable()) throw new Error('系统加密不可用，未保存 Key');
    updateDesktopSettings({ encryptedKey: safeStorage.encryptString(key).toString('base64') });
    await restartBackend();
    return publicDesktopSettings();
  });
  ipcMain.handle('key:delete', async () => {
    try { await apiRequest('DELETE', '/api/key'); } catch {}
    const saved = getSettings();
    delete saved.encryptedKey;
    writeJson(settingsFile(), saved);
    return publicDesktopSettings();
  });
  ipcMain.handle('key:check', () => apiRequest('POST', '/api/key/check', {}));
  ipcMain.handle('notification:test', () => notify('奶龙提醒', '这是一条本机测试通知'));
  ipcMain.on('app:open-external', (_event, url) => { if (/^https:\/\//.test(url)) shell.openExternal(url); });
  ipcMain.handle('dialog:error', (_event, message) => dialog.showMessageBox(mainWindow, { type: 'error', message: String(message).slice(0, 300) }));
  notificationTimer = setInterval(checkDueNotifications, 60000);
  setTimeout(checkDueNotifications, 3000);
  if (SMOKE_TEST) setTimeout(runSmokeTest, 1800);
}).catch((error) => {
  log(`[startup-error] ${error.message}`);
  app.exit(1);
});

app.on('activate', showMainWindow);
app.on('window-all-closed', () => {});
app.on('before-quit', async (event) => {
  if (isQuitting && !backend) return;
  event.preventDefault();
  isQuitting = true;
  if (notificationTimer) clearInterval(notificationTimer);
  if (cursorTimer) clearInterval(cursorTimer);
  if (disposeNativeClick) disposeNativeClick();
  saveWindowPosition();
  await stopBackend();
  app.exit(0);
});
