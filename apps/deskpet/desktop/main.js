const { app, BrowserWindow, Tray, Menu, Notification, nativeImage, ipcMain, safeStorage, screen, shell, dialog } = require('electron');
const { spawn } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const net = require('net');
const path = require('path');
const { installNativeClick } = require('./native-click');
// 打开外链的白名单只维护在 renderer/intents.js 一处：
// 渲染层先用它判意图，主进程再用它兜底校验（页面被改也绕不过去）。
const { isAllowedExternalUrl } = require('./renderer/intents.js');

const ROOT = path.resolve(__dirname, '../../..');
const PYTHON = path.join(ROOT, '.venv', 'Scripts', 'python.exe');
const WINDOW_SIZE = { width: 430, height: 760 };
const SMOKE_TEST = process.env.NAIWA_SMOKE_TEST === '1';

let mainWindow = null;
let tray = null;
let backend = null;
let apiPort = 0;
let currentUserId = '';
let isQuitting = false;
let mouseIgnored = true;
let cursorTimer = null;
let disposeNativeClick = null;
let positionSaveTimer = null;
let notificationTimer = null;
let backendGeneration = 0;
let restartChain = Promise.resolve();
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

// 用户身份只认 userData/settings.json 里这一份：
// 页面 origin 是 http://127.0.0.1:{动态端口}，每次重启后端端口都会变，
// 渲染进程 localStorage 跟着换分区——身份若只存 localStorage，
// 换一次 Key / 重开一次桌宠就会变成「认识你的第 1 天」。
const UID_PATTERN = /^[0-9a-f-]{36}$/i;

function ensureUserIdentity() {
  if (SMOKE_TEST) {
    // 自检必须每次都是「干净的新用户」：固定 uid 会让上一轮留下的待办
    // 污染 todoCount=0 的断言（业务库在 data/，不随 userData 一起清）。
    currentUserId = crypto.randomUUID();
    return currentUserId;
  }
  const saved = getSettings();
  if (typeof saved.userId === 'string' && UID_PATTERN.test(saved.userId)) {
    currentUserId = saved.userId;
    return currentUserId;
  }
  currentUserId = crypto.randomUUID();
  updateDesktopSettings({ userId: currentUserId });
  log(`[startup] 首次分配本机用户 id=${currentUserId}`);
  return currentUserId;
}

function appUrl() {
  const params = [`desktop=1`, `uid=${encodeURIComponent(currentUserId)}`];
  if (SMOKE_TEST) params.push('smoke=1');
  return `http://127.0.0.1:${apiPort}/?${params.join('&')}`;
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

// 串行化：删 Key / 改 Key 可能被连点或并发触发，两个重启交错会互相杀进程、
// 拿到两个端口、页面挂在前一个上（配完 Key 却请求旧端口的典型现场）。
function restartBackend() {
  const run = async () => {
    await stopBackend();
    await startBackend();
    if (mainWindow && !mainWindow.isDestroyed()) {
      await mainWindow.loadURL(appUrl());
    }
  };
  const next = restartChain.then(run, run);
  restartChain = next.catch(() => {});
  return next;
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
    // 页面里任何 window.open / target=_blank 一律走同一条白名单：
    // 只放 https + 白名单域名，http / file / javascript / 内网 全部拒绝。
    if (isAllowedExternalUrl(url)) shell.openExternal(url);
    else log(`[open-external] 拒绝打开：${String(url).slice(0, 120)}`);
    return { action: 'deny' };
  });
  mainWindow.once('ready-to-show', () => { if (!SMOKE_TEST) mainWindow.show(); });
  mainWindow.loadURL(appUrl());
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

// 每个弹窗一个 token，主进程按 token 派发结果，避免多窗口串台。
const pendingKeyDialogs = new Map();
// Agent 审批弹窗：同 key-dialog 范式（token + Map 派发）。
// 后端签发的一次性 approval_id 绑定精确参数；渲染层伪造的「已批准」进不了
// 后端（approve 接口只认它自己签发的 approval_id）。
const pendingApprovals = new Map();

// Agent 操作审批窗：展示待审批工具的参数原文（后端已掩码密钥），
// 返回与 action_requests 等长的 decisions 数组（按下标一一对应）。
function openApprovalDialog(payload) {
  return new Promise((resolve) => {
    const token = crypto.randomUUID();
    const dialogWindow = new BrowserWindow({
      width: 560, height: 520, useContentSize: true,
      parent: mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined,
      modal: Boolean(mainWindow && !mainWindow.isDestroyed()),
      show: false, resizable: true, minimizable: false, maximizable: false,
      fullscreenable: false, skipTaskbar: true,
      title: 'Agent 操作审批',
      webPreferences: {
        preload: path.join(__dirname, 'approval-dialog-preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true
      }
    });
    dialogWindow.setMenuBarVisibility(false);
    let settled = false;
    const finish = (decisions) => {
      if (settled) return;
      settled = true;
      pendingApprovals.delete(token);
      try { if (!dialogWindow.isDestroyed()) dialogWindow.destroy(); } catch {}
      resolve(decisions);   // null = 用户关窗/取消 → 渲染层按全部拒绝提交
    };
    pendingApprovals.set(token, finish);
    dialogWindow.once('ready-to-show', () => {
      dialogWindow.show();
      dialogWindow.focus();
    });
    dialogWindow.on('closed', () => finish(null));
    dialogWindow.webContents.on('did-finish-load', () => {
      dialogWindow.webContents.send('approval:payload', {
        token,
        approvalId: payload.approval_id,
        actionRequests: payload.action_requests || []
      });
    });
    dialogWindow.loadFile(path.join(__dirname, 'approval-dialog.html'), { query: { token } })
      .catch((error) => { log(`[approval] 审批窗加载失败：${error.message}`); finish(null); });
  });
}

// Key 输入框改成应用内模态窗口。
// 原来用 PowerShell 拉 WinForms：它跑在另一个进程里，窗口层级、窗口站、
// 执行策略任一不对劲就是「弹不出来 → Promise 永挂 → 点了没反应」，
// 而且失败了完全静默。应用内窗口跟主窗口同一个 parent，必然在奶龙上面，
// 也能被自动化测试直接驱动，不用再赌外部进程。
function promptForApiKey() {
  return new Promise((resolve) => {
    const token = crypto.randomUUID();
    const dialogWindow = new BrowserWindow({
      width: 520, height: 340, useContentSize: true,
      parent: mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined,
      modal: Boolean(mainWindow && !mainWindow.isDestroyed()),
      show: false, resizable: false, minimizable: false, maximizable: false,
      fullscreenable: false, skipTaskbar: true,
      title: '配置 DeepSeek API Key',
      webPreferences: {
        preload: path.join(__dirname, 'key-dialog-preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true
      }
    });
    dialogWindow.setMenuBarVisibility(false);
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      pendingKeyDialogs.delete(token);
      try { if (!dialogWindow.isDestroyed()) dialogWindow.destroy(); } catch {}
      resolve(String(value || '').trim().replace(/^Bearer\s+/i, ''));
    };
    pendingKeyDialogs.set(token, finish);
    dialogWindow.once('ready-to-show', () => {
      dialogWindow.show();
      dialogWindow.focus();
      dialogWindow.webContents.focus();
    });
    dialogWindow.on('closed', () => finish(''));
    dialogWindow.loadFile(path.join(__dirname, 'key-dialog.html'), { query: { token } })
      .catch((error) => { log(`[key] 输入框加载失败：${error.message}`); finish(''); });
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

const SMOKE_DIALOG_KEY = 'sk-smoke-dialog-probe-0000';

// 回归守卫：Key 输入框必须能弹出来、并且把用户输入交回主进程。
// 之前用 PowerShell 拉 WinForms —— 窗口建在别的窗口站、被置顶窗口挡、或外部
// 进程卡住时，Promise 永远不 settle，界面上只留一句「正在打开输入框」，
// 用户看到的就是「点了没反应」。这条守的是「点了必有一个能用的输入框」，
// 且不再依赖任何外部进程。
async function probeKeyDialog() {
  const pending = promptForApiKey();
  let dialog = null;
  for (let i = 0; i < 80; i += 1) {
    dialog = BrowserWindow.getAllWindows().find((win) => !win.isDestroyed()
      && win.webContents.getURL().includes('key-dialog.html'));
    if (dialog && !dialog.webContents.isLoading()) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!dialog) return { opened: false, submitted: null };
  try {
    await dialog.webContents.executeJavaScript(
      `(() => { const box = document.querySelector('#key');`
      + ` box.value = ${JSON.stringify(SMOKE_DIALOG_KEY)};`
      + ' document.querySelector("#save").click(); return true; })()');
  } catch (error) {
    return { opened: true, submitted: null, error: error.message };
  }
  const value = await Promise.race([
    pending,
    new Promise((resolve) => setTimeout(() => resolve('__TIMEOUT__'), 5000))
  ]);
  return { opened: true, submitted: value };
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
      // 样例按钮由渲染层异步生成（app.js renderNoticeSamples → dataset.sample），
      // 固定 sleep 会跟它抢跑：启动慢一点就 null.click() 直接抛错。
      for (let i = 0; i < 100 && !$('[data-sample]'); i += 1) await wait(100);
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
        // 会话 5：小游戏下架，这里顺手记下「动作调度层在线」的证据
        actionKeys: Object.keys(window.ActionDirector.ACTIONS).length,
        actionNow: document.querySelector('#petScene').dataset.action,
        modelCalls: (await (await fetch('/api/debug/model-calls')).json()).total
      };
    })()`);
    Object.assign(evidence, ui);
    await mainWindow.reload();
    await new Promise((resolve) => setTimeout(resolve, 900));
    evidence.refreshRealDays = await mainWindow.webContents.executeJavaScript(`document.querySelector('#daysBadge').textContent`);
    evidence.refreshCollapsed = await mainWindow.webContents.executeJavaScript(`document.querySelector('#panel').hidden`);
    evidence.noKeyStatus = await apiRequest('GET', '/api/key/status');
    evidence.keyDialog = await probeKeyDialog();
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
    // 与 key:delete 的真实顺序一致：先让后端清 env，再删加密份，最后换进程
    try { await apiRequest('DELETE', '/api/key'); } catch {}
    const afterDelete = getSettings();
    delete afterDelete.encryptedKey;
    writeJson(settingsFile(), afterDelete);
    evidence.deletedKeyStatusBeforeRestart = await apiRequest('GET', '/api/key/status');
    await restartBackend();
    await new Promise((resolve) => setTimeout(resolve, 700));
    evidence.deletedKeyStatus = await apiRequest('GET', '/api/key/status');
    evidence.publicAfterDelete = publicDesktopSettings();
    evidence.uidAfterRestart = await mainWindow.webContents.executeJavaScript(
      `localStorage.getItem('deskpet_uid')`);
    evidence.sameUserAfterRestart = evidence.uidAfterRestart === currentUserId;
    evidence.deletedModelCalls = (await apiRequest('GET', '/api/debug/model-calls')).total;

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
      ui.actionKeys >= 8 && ui.modelCalls === 0 && evidence.refreshRealDays.includes('第 1 天') &&
      evidence.refreshCollapsed && evidence.noKeyStatus.configured === false && evidence.dynamicPort &&
      evidence.keyDialog && evidence.keyDialog.opened && evidence.keyDialog.submitted === SMOKE_DIALOG_KEY &&
      evidence.loopbackOnly && evidence.injectedKeyStatus.configured === true &&
      evidence.injectedKeyStatus.masked_tail === secret.slice(-4) &&
      !evidence.keyLeakChecks.renderer && !evidence.keyLeakChecks.commandLine && !evidence.keyLeakChecks.logs &&
      evidence.injectedModelCalls === 0 && evidence.deletedKeyStatusBeforeRestart.configured === false &&
      evidence.deletedKeyStatus.configured === false &&
      evidence.sameUserAfterRestart &&
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
  ensureUserIdentity();
  await startBackend();
  createTray();
  createWindow();
  ipcMain.handle('runtime:get', () => ({ host: '127.0.0.1', port: apiPort }));
  ipcMain.handle('desktop:settings:get', () => publicDesktopSettings());
  ipcMain.on('user:identity', (_event, uid) => {
    // 身份以主进程这份为准（随 URL 下发给页面）；页面回传只做一致性校验，
    // 不允许另一份 uid 覆盖，否则重启后又分裂成两个「用户」。
    if (typeof uid === 'string' && UID_PATTERN.test(uid) && uid !== currentUserId) {
      log(`[identity] 忽略页面回传的陌生 uid=${uid}`);
    }
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
  ipcMain.on('key-dialog:result', (event, payload) => {
    if (!payload || typeof payload.token !== 'string') return;
    // token 是每次弹窗随机生成的，只存在于主进程映射表里；
    // 查不到就忽略，页面里别的东西没法靠伪造 action 触发「已保存」
    const finish = pendingKeyDialogs.get(payload.token);
    if (typeof finish !== 'function') return;
    finish(payload.action === 'submit' ? payload.value : '');
  });
  ipcMain.handle('approval:request', (_event, payload) => {
    // 渲染层只能请求弹窗，不能自己宣布结果：决定必须经真实窗口回来
    if (!payload || !Array.isArray(payload.action_requests)) return null;
    return openApprovalDialog(payload);
  });
  ipcMain.on('approval-dialog:result', (_event, payload) => {
    if (!payload || typeof payload.token !== 'string') return;
    const finish = pendingApprovals.get(payload.token);
    if (typeof finish !== 'function') return;
    if (payload.action === 'decide' && Array.isArray(payload.decisions)) {
      const clean = payload.decisions.map((d) =>
        (d && d.type === 'approve') ? { type: 'approve' } : { type: 'reject' });
      finish(clean);
    } else {
      finish(null);   // 取消/关窗 → 渲染层按全部拒绝提交
    }
  });
  ipcMain.handle('key:change', async () => {
    const key = await promptForApiKey();
    // 用户取消不是失败，也不是成功：如实回一个 saved=false，界面别撒谎
    if (!key) return { ...publicDesktopSettings(), saved: false };
    if (!safeStorage.isEncryptionAvailable()) throw new Error('系统加密不可用，未保存 Key');
    updateDesktopSettings({ encryptedKey: safeStorage.encryptString(key).toString('base64') });
    // 换 Key 必须换进程：Python 子进程的 env 是 spawn 那一刻拷进去的，
    // 不重启的话新 Key 永远进不了后端（旧 Key 也永远出不去）。
    await restartBackend();
    return { ...publicDesktopSettings(), saved: true };
  });
  ipcMain.handle('key:delete', async () => {
    // 三条都要做，缺一条就是「删了 Key 却还能调模型」：
    // 1) 通知后端立刻清掉注入的环境变量并重置懒加载态（不等重启）；
    // 2) 删掉 userData 里的 safeStorage 加密份；
    // 3) 重启后端子进程——旧 env 只在进程内存里，只有换进程才算真删干净。
    try { await apiRequest('DELETE', '/api/key'); } catch (error) { log(`[key] DELETE /api/key 失败，靠重启兜底：${error.message}`); }
    const saved = getSettings();
    delete saved.encryptedKey;
    writeJson(settingsFile(), saved);
    await restartBackend();
    return publicDesktopSettings();
  });
  ipcMain.handle('key:check', () => apiRequest('POST', '/api/key/check', {}));
  ipcMain.handle('notification:test', () => notify('奶龙提醒', '这是一条本机测试通知'));
  ipcMain.on('app:open-external', (_event, url) => {
    if (isAllowedExternalUrl(url)) shell.openExternal(url);
    else log(`[open-external] 拒绝打开：${String(url).slice(0, 120)}`);
  });
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
