(() => {
  'use strict';

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  // 阶段 9：后端 deadline 是带时区的 ISO，<input type="datetime-local"> 只认本地
  // 时区的 YYYY-MM-DDTHH:mm。绝不能用 toISOString().slice(0,16)——那是 UTC，会差 8 小时。
  const toLocalInput = (iso) => {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    const p = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}T${p(date.getHours())}:${p(date.getMinutes())}`;
  };
  // 会话 5：动作调度的常量从 actions.js 取（单一事实源，不在两处各写一份优先级）
  const { ActionDirector, PRIORITY } = window;
  const intents = window.DeskpetIntents || { detectOpenIntent: () => null, detectTaskIntent: () => null };

  class Api {
    constructor() {
      // 桌面壳每次拉起后端都换动态端口，localStorage 跟着换分区；
      // 身份统一由主进程随 URL 下发，localStorage 只做兜底。
      const key = 'deskpet_uid';
      const fromShell = new URLSearchParams(location.search).get('uid');
      this.uid = fromShell && /^[0-9a-f-]{36}$/i.test(fromShell)
        ? fromShell
        : localStorage.getItem(key);
      if (!this.uid) this.uid = crypto.randomUUID();
      localStorage.setItem(key, this.uid);
    }

    async request(path, options = {}) {
      const response = await fetch(`/api${path}`, {
        ...options,
        headers: { 'X-User-Id': this.uid, 'Content-Type': 'application/json', ...(options.headers || {}) }
      });
      let data = {};
      try { data = await response.json(); } catch {}
      if (!response.ok) throw new Error(data.detail || `本地请求失败（${response.status}）`);
      return data;
    }

    get(path) { return this.request(path); }
    post(path, body = {}) { return this.request(path, { method: 'POST', body: JSON.stringify(body) }); }
    patch(path, body) { return this.request(path, { method: 'PATCH', body: JSON.stringify(body) }); }
    delete(path) { return this.request(path, { method: 'DELETE' }); }
  }

  class UiStateMachine {
    constructor() { this.state = 'collapsed'; }

    transition(next) {
      const allowed = this.state === 'collapsed'
        ? ['open']
        : ['collapsed', 'open', 'notice', 'todos', 'settings', 'about', 'checklist'];
      if (!allowed.includes(next)) return false;
      this.state = next;
      return true;
    }
  }

  class DeskpetApp {
    constructor() {
      this.api = new Api();
      this.shell = window.deskpetShell || null;
      this.machine = new UiStateMachine();
      this.pet = new PetController($('#petCanvas'), this.shell, () => this.activatePet());
      // 任务小画面必须先就位：ActionDirector 构造时就会同步回调 onChange，
      // 那一下会读 this.taskScene（顺序反了 = 开局就抛 undefined）。
      this.taskScene = new TaskScene($('#taskScene'));
      this.taskTimer = null;
      // 会话 5：动作调度层。所有动作切换都走 this.director.play()，
      // 不再各处散调 this.pet.switchTo()（散调就会互相抢帧、动作来回闪）。
      this.director = new ActionDirector({
        pet: this.pet,
        scene: $('#petScene'),
        onChange: (key) => this.onActionChange(key)
      });
      this.todoCount = 0;
      this.todos = [];
      this.growth = null;
      this.checklist = [];
      this.deadlineHint = null;
      this.keyConfigured = false;
      this.keyPromptDismissed = false;
      this.decisionToken = 0;
      this.toastTimer = null;
      this.bubbleTimer = null;
      this.pointer = null;
      this.todosLoaded = false;
      // 阶段 9：待办行下方的问答区，同一时刻只允许一个展开
      this.qaOpen = null;
      // ---- 会话 3：hover 展开的聊天框 ----
      this.chatSession = crypto.randomUUID ? crypto.randomUUID() : `chat-${Date.now()}`;
      this.chatTurns = [];       // 最近 ≤6 轮，回传做 history 播种（后端有记录时以后端为准）
      this.chatBusy = false;     // 请求在飞：锁住展开态，别让框在等回复时收起来
      this.chatPinned = false;   // 输入框有焦点/有内容：同样锁住
      // ---- 会话 4：完整 Agent 第四态（默认关闭） ----
      this.agentMode = false;    // 只有显式打开才走 /agent/chat
      this.approvalBusy = false; // 审批窗在飞：防重复弹
    }

    async init() {
      if (document.body.classList.contains('desktop') === false && new URLSearchParams(location.search).has('desktop')) {
        document.body.classList.add('desktop');
      }
      this.bindEvents();
      if (this.shell) {
        this.shell.identify(this.api.uid);
        // 兼容两种形态：旧的 'open' 字符串，以及会话 5 的结构化命令对象
        this.shell.onCommand((command) => this.onShellCommand(command));
        this.shell.onSettings((settings) => this.applyDesktopSettings(settings));
        this.shell.getSettings().then((settings) => this.applyDesktopSettings(settings)).catch(() => {});
      }
      // 动作 ttl 的到期判定挂在渲染循环上（不另起定时器，也不跟 pet.js 的 rAF 打架）
      const pump = (now) => { this.director.tick(now); requestAnimationFrame(pump); };
      requestAnimationFrame(pump);
      const jobs = [
        this.pet.load().catch((error) => this.setBubble(error.message)),
        this.api.post('/growth/simulate/clear').then(() => this.loadGrowth()).catch(() => this.setBubble('本地成长数据暂时读不到，点我仍可打开清单。')),
        this.loadTodos().catch(() => {
          const count = $('#bubbleCount');
          if (count) count.textContent = '待办暂时没读到';
        }),
        this.loadNotices().catch(() => this.toast('样例通知暂时不可用')),
        this.loadKeyStatus().catch(() => {}),
        this.loadModelSwitch().catch(() => {}),
        this.syncAgentMode().catch(() => {})
      ];
      await Promise.allSettled(jobs);
      this.updateBubbleSummary();
    }

    // ---- 会话 5：主进程 → 渲染层的命令（结构化对象，旧字符串仍兼容） ----
    onShellCommand(command) {
      if (!command) return;
      if (command === 'open') { this.openPanel(); return; }
      if (typeof command !== 'object') return;
      if (command.type === 'lift') { this.handleLift(command); return; }
      if (command.type === 'open') this.openPanel();
    }

    // 往上拖（主进程 native-click.js 判位移 ≥6px 且 dy<0 才发）：
    // 抬起 → lifted，松手 → land 再回 idle。
    handleLift({ phase, dy } = {}) {
      const moved = Number(dy) || 0;
      if (phase === 'begin' || phase === 'move') {
        if (moved <= -6 || phase === 'move') {
          this.director.play('lifted', { source: 'drag' });
        }
        return;
      }
      if (phase === 'end') {
        // 只有当动作确实还挂在拖拽来源上才落一次地，避免松手后多播一次
        if (this.director.active && this.director.active.source === 'drag') {
          // 同源接管：land 直接顶掉自己占着的 lifted（跨来源的低优先级只能排队）
          this.director.play('land', { ttl: 780, source: 'drag' });
        }
      }
    }

    // 动作切换的副作用：思考态挂任务小画面，其余收起来
    onActionChange(key) {
      if (!this.taskScene) return;      // 构造期回调兜底（director 先于部分字段就位）
      if (key === 'thinking') return;   // 小画面由任务演出单独控制，别在纯思考时闪一下
      if (this.taskScene.visible && ['lifted', 'land', 'idle'].includes(key)) this.hideTaskScene();
    }

    showTaskScene(kind, label) {
      this.taskScene.show(kind, label);
      clearTimeout(this.taskTimer);
      // 演出跑完自动收起（不留在桌面上挡视线）
      this.taskTimer = setTimeout(() => this.hideTaskScene(), 5200);
    }

    hideTaskScene() {
      clearTimeout(this.taskTimer);
      this.taskTimer = null;
      this.taskScene.hide();
    }

    bindEvents() {
      $('#closePanel').addEventListener('click', () => this.closePanel());
      $$('.panel-tabs [data-view]').forEach((button) => button.addEventListener('click', () => this.showView(button.dataset.view)));
      $$('[data-simulate]').forEach((button) => button.addEventListener('click', () => this.simulate(Number(button.dataset.simulate))));
      $('#clearSimulation').addEventListener('click', () => this.clearSimulation());
      $('#parseNotice').addEventListener('click', () => this.parseNotice());
      $('#clearNotice').addEventListener('click', () => { $('#noticeText').value = ''; $('#checklist').replaceChildren(); $('#confirmChecklist').disabled = true; });
      $('#confirmChecklist').addEventListener('click', () => this.confirmChecklist());
      $('#exportDocx').addEventListener('click', () => this.exportDocx());
      $('#addTodo').addEventListener('click', () => this.addTodo());
      $('#voiceParse').addEventListener('click', () => this.parseVoice());
      $('#voiceText').addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        this.parseVoice();
      });
      $('#laterKey').addEventListener('click', () => { this.keyPromptDismissed = true; $('#keyPrompt').hidden = true; });
      $('#configureKey').addEventListener('click', () => { this.openPanel(); this.showView('settings'); });
      $('#changeKey').addEventListener('click', () => this.changeKey());
      $('#deleteKey').addEventListener('click', () => this.deleteKey());
      $('#checkKey').addEventListener('click', () => this.checkKey());
      $('#modelSwitch').addEventListener('change', (event) => this.setModelSwitch(event.target.value));
      $('#alwaysOnTop').addEventListener('change', (event) => { if (this.shell) this.shell.setAlwaysOnTop(event.target.checked); });
      $('#muted').addEventListener('change', (event) => { if (this.shell) this.shell.setMuted(event.target.checked); });
      $('#nightTheme').addEventListener('change', (event) => this.setTheme(event.target.checked ? 'night' : 'day'));
      $('#testNotification').addEventListener('click', async () => {
        if (this.shell) await this.shell.testNotification();
        else this.toast('浏览器预览不弹系统通知，桌面壳内可用');
      });
      $('#zoomPet').addEventListener('click', () => $('#petCanvas').classList.toggle('zoomed'));
      $$('[data-external]').forEach((link) => link.addEventListener('click', (event) => {
        if (this.shell) { event.preventDefault(); this.shell.openExternal(link.href); }
      }));
      this.bindBubbleHover();
      this.bindChat();
      this.bindAgentMode();
    }

    activatePet() {
      this.director.play('wave', { source: 'activate' });
      if (this.machine.state === 'collapsed') this.openPanel();
      else this.closePanel();
    }

    openPanel() {
      if (this.machine.state === 'collapsed') this.machine.transition('open');
      $('#panel').hidden = false;
      if (this.machine.state === 'open') this.showView('notice');
    }

    closePanel() {
      this.machine.transition('collapsed');
      $('#panel').hidden = true;
      // 收起面板 = 交还动作控制权：回 idle，空闲轮播由 pet.js 自己接手
      this.director.reset();
    }

    showView(name) {
      if (this.machine.state === 'collapsed') this.machine.transition('open');
      this.machine.transition(name);
      $('#panel').hidden = false;
      $$('.view').forEach((view) => view.classList.toggle('active', view.id === `${name}View`));
      $$('.panel-tabs [data-view]').forEach((button) => button.classList.toggle('active', button.dataset.view === name));
      if (name === 'todos') this.loadTodos().catch(() => {});
      if (name === 'settings') { this.loadKeyStatus().catch(() => {}); this.loadModelSwitch().catch(() => {}); }
    }

    setBubble(text, note = '', degraded = false) {
      $('#bubbleText').textContent = text;
      $('#bubbleNote').textContent = note;
      $('#bubble').classList.toggle('degraded', degraded);
    }

    // ---- 气泡：常驻计数 + hover 展开详情 ----

    pendingTodos() {
      return (this.todos || []).filter((todo) => todo.status !== 'done');
    }

    updateBubbleSummary() {
      if (!this.todosLoaded) return;
      const pending = this.pendingTodos();
      const count = $('#bubbleCount');
      if (count) count.textContent = `您还有 ${pending.length} 件待办事件`;
      const nudge = $('#bubbleNudge');
      if (nudge) nudge.textContent = pending.length ? '' : '先歇一会儿吧';
      this.renderTodoStrip(pending.map((todo) => todo.title));
      this.setBubble(this.reminderLine(pending), this.nearestDeadline(pending));
      // 主动关心：复用「待办刷新」这个既有触发点，纯规则生成的关心话术，
      // 零模型调用（不新起后台 Tick，也不为这句话花 token）。
      const care = $('#chatCare');
      if (care) care.textContent = this.chatCareText();
    }

    // 规则生成的关心话术（只看本地已加载的待办，不调模型）
    chatCareText() {
      const pending = this.pendingTodos();
      const titles = pending.map((todo) => String(todo.title || '').trim()).filter(Boolean);
      if (!titles.length) return '清单是空的，先歇一会儿吧';
      const times = pending
        .map((todo) => (todo.deadline ? new Date(todo.deadline).getTime() : NaN))
        .filter((value) => Number.isFinite(value));
      if (times.length) {
        return `最近一件在 ${new Date(Math.min(...times)).toLocaleString('zh-CN')} 前`;
      }
      if (titles.length === 1) return `还剩「${titles[0]}」这一件`;
      return `还有 ${titles.length} 件没做完，一件件来`;
    }

    reminderLine(pending) {
      const titles = pending.map((todo) => String(todo.title || '').trim()).filter(Boolean);
      if (!titles.length) return '眼下没有待办，想歇一会儿就歇一会儿。';
      if (titles.length === 1) return `接下来要做的：${titles[0]}。我陪你慢慢来。`;
      const head = titles.slice(0, 2).join('、');
      const rest = titles.length > 2 ? `，还有 ${titles.length - 2} 件` : '';
      return `接下来要做的：${head}${rest}。我陪你一件件来。`;
    }

    nearestDeadline(pending) {
      const times = pending
        .map((todo) => (todo.deadline ? new Date(todo.deadline).getTime() : NaN))
        .filter((value) => Number.isFinite(value));
      if (!times.length) return '';
      return `最近的一件在 ${new Date(Math.min(...times)).toLocaleString('zh-CN')} 前`;
    }

    // 四小格直接写待办条目，不再放流程解说
    renderTodoStrip(titles) {
      const list = $('#decisionChain');
      if (!list || typeof list.replaceChildren !== 'function') return;
      const entries = (titles || [])
        .map((title) => String(title || '').trim())
        .filter(Boolean)
        .slice(0, 4);
      list.replaceChildren();
      list.hidden = entries.length === 0;
      entries.forEach((title, index) => {
        const cell = document.createElement('li');
        cell.className = 'decision-cell';
        const order = document.createElement('span');
        order.textContent = String(index + 1);
        const text = document.createElement('p');
        text.textContent = title;
        cell.append(order, text);
        list.append(cell);
      });
    }

    bindBubbleHover() {
      const bubble = $('#bubble');
      if (!bubble) return;
      const expand = () => {
        clearTimeout(this.bubbleTimer);
        this.bubbleTimer = null;
        this.setBubbleExpanded(true);
      };
      const leave = () => {
        clearTimeout(this.bubbleTimer);
        this.bubbleTimer = null;
        this.scheduleBubbleCollapse();
      };
      ['#bubbleChip', '#bubbleDetail'].forEach((selector) => {
        const node = $(selector);
        if (!node || typeof node.addEventListener !== 'function') return;
        node.addEventListener('mouseenter', expand);
        node.addEventListener('mouseleave', leave);
      });
      if (this.shell) {
        this.shell.onCursor((point) => {
          this.pointer = point;
          this.syncBubbleHover();
        });
      }
    }

    setBubbleExpanded(expanded) {
      const bubble = $('#bubble');
      const detail = $('#bubbleDetail');
      if (!bubble || !detail) return;
      const next = expanded ? '1' : '0';
      if (bubble.dataset.expanded === next) return;
      bubble.dataset.expanded = next;
      detail.setAttribute('aria-hidden', expanded ? 'false' : 'true');
      if (expanded) {
        const care = $('#chatCare');
        if (care) care.textContent = this.chatCareText();
      } else {
        // 收起态不抢焦点：把输入框的焦点交出去，免得穿透态下还在收键盘事件
        const input = $('#chatInput');
        if (input && document.activeElement === input) input.blur();
      }
    }

    bubbleHoverTarget(point) {
      const bubble = $('#bubble');
      if (!bubble || typeof bubble.getBoundingClientRect !== 'function') return false;
      if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return false;
      const rect = bubble.getBoundingClientRect();
      const pad = 10;
      return point.x >= rect.left - pad && point.x <= rect.right + pad
        && point.y >= rect.top - pad && point.y <= rect.bottom + pad;
    }

    scheduleBubbleCollapse() {
      const bubble = $('#bubble');
      if (!bubble || bubble.dataset.expanded !== '1' || this.bubbleTimer) return;
      // 打字时锁住展开态：输入框有焦点/有内容，或者回复还在路上，都不收
      if (this.chatPinned || this.chatBusy) return;
      this.bubbleTimer = setTimeout(() => {
        this.bubbleTimer = null;
        if (this.chatPinned || this.chatBusy) return;
        if (this.bubbleHoverTarget(this.pointer)) return;
        this.setBubbleExpanded(false);
      }, 180);
    }

    // 鼠标每 50ms 会被主进程回传一次坐标：hover 在穿透态下也能据此收敛，避免卡住或抖动
    syncBubbleHover() {
      const bubble = $('#bubble');
      if (!bubble) return;
      if (this.bubbleHoverTarget(this.pointer)) {
        clearTimeout(this.bubbleTimer);
        this.bubbleTimer = null;
        this.setBubbleExpanded(true);
        return;
      }
      this.scheduleBubbleCollapse();
    }

    // ---- hover 展开的小聊天框（会话 3） ----

    bindChat() {
      const input = $('#chatInput');
      const send = $('#chatSend');
      if (!input || !send) return;
      input.addEventListener('input', () => this.updateChatSend());
      input.addEventListener('focus', () => this.setChatPinned(true));
      input.addEventListener('blur', () => {
        // 正在等回复、或者框里还有字，都不许自动收起（打到一半框没了最烦）
        this.setChatPinned(this.chatBusy || Boolean(input.value.trim()));
      });
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          this.sendChat();
        }
      });
      send.addEventListener('click', () => this.sendChat());
      this.updateChatSend();
    }

    setChatPinned(pinned) {
      this.chatPinned = Boolean(pinned);
      if (this.chatPinned) {
        clearTimeout(this.bubbleTimer);
        this.bubbleTimer = null;
        this.setBubbleExpanded(true);
        return;
      }
      this.scheduleBubbleCollapse();
    }

    updateChatSend() {
      const input = $('#chatInput');
      const send = $('#chatSend');
      if (!input || !send) return;
      send.disabled = this.chatBusy || !input.value.trim();
    }

    appendChat(kind, text) {
      const log = $('#chatLog');
      if (!log) return null;
      const item = document.createElement('li');
      item.className = `chat-msg ${kind}`;
      const body = document.createElement('p');
      // 模型输出一律 textContent：CSP 禁 inline script，也绝不 innerHTML 拼回复
      body.textContent = String(text || '');
      item.append(body);
      log.append(item);
      log.scrollTop = log.scrollHeight;
      return item;
    }

    // 等回复时只在气泡里放三个点，不写任何来源/降级说明小字
    showChatTyping() {
      const log = $('#chatLog');
      if (!log) return null;
      const item = document.createElement('li');
      item.className = 'chat-msg pet chat-typing';
      for (let i = 0; i < 3; i += 1) item.append(document.createElement('i'));
      log.append(item);
      log.scrollTop = log.scrollHeight;
      return item;
    }

    clearChatTyping(item) {
      if (item && item.parentNode) item.parentNode.removeChild(item);
    }

    chatMoodAnimation(mood) {
      // mood 只影响表情/语气，不改任何文字内容；未知值回 idle。
      // 返回值是 actions.js 里的动作键（anim 映射仍只有这一份，不再另写一套）。
      const table = {
        cheer: 'celebrate',
        proud: 'celebrate',
        playful: 'shy',
        encourage: 'wave',
        concerned: 'sit',
        calm: 'idle'
      };
      return table[mood] || 'idle';
    }

    // ---- 会话 5：思考态 + 本地意图（都不烧模型） ----
    beginThinking(source) {
      // ttl 是兜底：请求挂死也必须能自己回落，不许永远卡在思考态
      this.director.play('thinking', { ttl: 15000, source });
    }

    endThinking(source) {
      this.director.release(source);
    }

    // 「打开网址」「玩一局/写文档」在本地就判掉：不等后端、不花 token。
    async handleLocalIntent(text) {
      const open = intents.detectOpenIntent(text);
      if (open) {
        this.appendChat('user', text);
        if (open.allowed) {
          this.director.play('wave', { ttl: 1800, source: 'open-url' });
          if (this.shell) this.shell.openExternal(open.url);
          this.appendChat('pet', `好，我这就替你打开 ${open.url}。`);
        } else {
          this.director.play('shy', { ttl: 2000, source: 'open-url' });
          this.appendChat('pet',
            '这个链接我没敢开：只认 https 且在我白名单里的站点，'
            + 'http、内网地址、脚本链接一律不碰。');
        }
        return true;
      }
      const task = intents.detectTaskIntent(text);
      if (task) {
        this.appendChat('user', text);
        this.runTaskPerformance(task);
        return true;
      }
      return false;
    }

    // 拟人任务演出：thinking → work → celebrate，旁边一块纯 DOM 小画面。
    // 注意：这里**不启动任何浏览器、不控制真实网页**，小画面只是缩略示意。
    async runTaskPerformance(task) {
      this.showTaskScene(task.kind, task.label);
      this.director.play('thinking', { ttl: 2000, source: 'task' });
      await wait(1000);
      this.director.play('work', { ttl: 2600, source: 'task' });
      await wait(1700);
      this.director.play('celebrate', { ttl: 2000, source: 'task' });
      this.appendChat('pet', task.kind === 'play'
        ? '一局走完了，我一直在旁边看着（纯规则局，不开浏览器）。'
        : '文档骨架搭好了，等你点确认，我按清单生成 docx。');
      await wait(1900);
      this.director.release('task');
    }

    async sendChat() {
      const input = $('#chatInput');
      if (!input) return;
      const text = input.value.trim();
      if (!text || this.chatBusy) return;
      // 会话 4：Agent 模式打开 → 走完整 Agent（全工具、需审批、可联网）
      if (this.agentMode) {
        await this.sendAgentChat(text);
        return;
      }
      // 会话 5：本地意图优先（打开网址 / 玩一局 / 写文档）
      if (await this.handleLocalIntent(text)) { input.value = ''; return; }
      input.value = '';
      this.chatBusy = true;
      this.setChatPinned(true);
      this.updateChatSend();
      this.appendChat('user', text);
      this.chatTurns.push({ role: 'user', content: text });
      this.chatTurns = this.chatTurns.slice(-12);
      const typing = this.showChatTyping();
      // 请求一发出立刻进思考态：不等后端（关态走规则回复同样有思考态）
      this.beginThinking('chat');
      try {
        const result = await this.api.post('/chat', {
          message: text,
          session_id: this.chatSession,
          history: this.chatTurns.slice(-12)
        });
        this.clearChatTyping(typing);
        const reply = (result.data && result.data.reply) || '……';
        this.appendChat('pet', reply);
        this.chatTurns.push({ role: 'assistant', content: reply });
        this.chatTurns = this.chatTurns.slice(-12);
        this.endThinking('chat');
        if (result.data && result.data.mood) {
          this.director.play(this.chatMoodAnimation(result.data.mood),
            { priority: PRIORITY.MOOD, ttl: 2200, source: 'chat-mood' });
        }
      } catch (error) {
        this.clearChatTyping(typing);
        this.endThinking('chat');
        this.appendChat('pet', `本地服务暂时没回应：${error.message}`);
      } finally {
        this.clearChatTyping(typing);
        this.endThinking('chat');   // 失败 / 超时也一定退出思考态，不许卡死
        this.chatBusy = false;
        // 把焦点还给输入框：连续聊天时框不该因为一次发送就收起
        try { input.focus(); } catch {}
        this.setChatPinned(true);
        this.updateChatSend();
      }
    }

    // ---- 会话 4：完整 Agent 第四态 ----
    // 来源标注四态如实：模型回 / 规则兜底 / 降级 / 来自网络，绝不混写。
    bindAgentMode() {
      const toggle = $('#agentMode');
      if (!toggle) return;
      toggle.addEventListener('change', async () => {
        const next = Boolean(toggle.checked);
        try {
          await this.api.post('/agent/mode', { enabled: next });
          this.agentMode = next;
          toggle.closest('.agent-toggle').classList.toggle('on', next);
          this.toast(next
            ? 'Agent 模式已打开：可以联网、用工具；写文件/执行命令前会先问你'
            : 'Agent 模式已关闭：回到普通聊天');
        } catch (error) {
          toggle.checked = this.agentMode;   // 失败回滚开关
          this.toast(`切换失败：${error.message}`);
        }
      });
    }

    async syncAgentMode() {
      try {
        const status = await this.api.get('/agent/status');
        this.agentMode = Boolean(status.agent_mode);
        const toggle = $('#agentMode');
        if (toggle) {
          toggle.checked = this.agentMode;
          toggle.closest('.agent-toggle').classList.toggle('on', this.agentMode);
        }
      } catch {}   // 状态读不到就保持默认关，不打扰主流程
    }

    // 审批循环：pending_approval → 弹窗（无壳环境自动按拒绝处理）→ 提交决定
    // → 可能又是 pending（模型接着要批准别的）→ 最多 3 轮防绕圈。
    async resolveApprovals(first) {
      let current = first;
      for (let round = 0; round < 3 && current
             && current.status === 'pending_approval'; round += 1) {
        const requests = current.action_requests || [];
        const names = requests.map((r) => r.name).join('、');
        this.appendChat('pet',
          `我想${requests.length > 1 ? '分别' : ''}执行：${names}。参数在弹窗里贴出来了，`
          + '60 秒不回应我按拒绝处理，等你决定。');
        let decisions = null;
        if (this.shell && !this.approvalBusy) {
          this.approvalBusy = true;
          try {
            decisions = await this.shell.requestApproval({
              approval_id: current.approval_id,
              action_requests: requests
            });
          } finally {
            this.approvalBusy = false;
          }
        } else if (!this.shell) {
          this.appendChat('pet',
            '审批弹窗只在桌面壳内可用；这里我按「拒绝」处理，操作不会执行。');
        }
        if (!decisions) decisions = requests.map(() => ({ type: 'reject' }));
        current = await this.api.post('/agent/approve', {
          approval_id: current.approval_id,
          decisions
        });
      }
      return current;
    }

    async sendAgentChat(text) {
      const input = $('#chatInput');
      // 会话 5：Agent 链路同样先处理本地意图（打开网址在 Agent 模式下也走同一条白名单）
      if (await this.handleLocalIntent(text)) { input.value = ''; return; }
      input.value = '';
      this.chatBusy = true;
      this.setChatPinned(true);
      this.updateChatSend();
      this.appendChat('user', text);
      this.chatTurns.push({ role: 'user', content: text });
      this.chatTurns = this.chatTurns.slice(-12);
      const typing = this.showChatTyping();
      this.beginThinking('agent');
      try {
        let result = await this.api.post('/agent/chat', {
          message: text,
          session_id: this.chatSession
        });
        this.clearChatTyping(typing);
        // 审批循环期间继续显示思考态（模型在等你的决定，不是在发呆）
        this.beginThinking('agent');
        result = await this.resolveApprovals(result);
        const reply = result.status === 'ok'
          ? (result.reply || '……')
          : (result.reply || result.degrade_reason || '这次没做成，等下再试。');
        this.appendChat('pet', reply);
        this.chatTurns.push({ role: 'assistant', content: reply });
        this.chatTurns = this.chatTurns.slice(-12);
        this.endThinking('agent');
        this.director.play(result.status === 'ok' ? 'celebrate' : 'shy',
          { ttl: 2000, source: 'agent-mood' });
      } catch (error) {
        this.clearChatTyping(typing);
        this.appendChat('pet', `本地服务暂时没回应：${error.message}`);
      } finally {
        this.clearChatTyping(typing);
        this.endThinking('agent');
        this.chatBusy = false;
        try { input.focus(); } catch {}
        this.setChatPinned(true);
        this.updateChatSend();
      }
    }

    toast(message) {
      const toast = $('#toast');
      toast.textContent = message;
      toast.classList.add('show');
      clearTimeout(this.toastTimer);
      this.toastTimer = setTimeout(() => toast.classList.remove('show'), 2600);
    }

    async loadGrowth() {
      const result = await this.api.get('/growth');
      this.renderGrowth(result.data);
    }

    renderGrowth(growth) {
      this.growth = growth;
      const displayDay = Math.max(1, growth.days_known);
      const text = `认识你的第 ${displayDay} 天`;
      $('#daysBadge').textContent = text;
      $('#panelDays').textContent = text;
      $('#stageText').textContent = growth.stage || '初识';
    }

    async simulate(day) {
      const result = await this.api.post('/growth/simulate', { target_day: day });
      this.renderGrowth(result.data);
      this.director.play(day >= 30 ? 'celebrate' : 'shy',
        { ttl: 2400, source: 'simulate' });
      this.setBubble(`现在只是展示第 ${day} 天的样子，刷新就回真实天数。`, '模拟展示，不写历史');
    }

    async clearSimulation() {
      const result = await this.api.post('/growth/simulate/clear');
      this.renderGrowth(result.data);
      this.director.reset();
      this.setBubble('回来啦，这里显示的是真实认识天数。');
    }

    async loadNotices() {
      const response = await fetch('/static/notices.json');
      const data = await response.json();
      const container = $('#noticeSamples');
      container.replaceChildren();
      data.notices.forEach((notice) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset.sample = notice.id;
        button.textContent = notice.title;
        button.title = '只填入文本，不写库';
        button.addEventListener('click', () => { $('#noticeText').value = notice.text; });
        container.append(button);
      });
    }

    async loadTodos() {
      const result = await this.api.get('/todos');
      this.todos = result.data || [];
      this.todosLoaded = true;
      $('#todoCount').textContent = String(this.todos.length);
      const list = $('#todoList');
      list.replaceChildren();
      // 列表重建 = 旧的问答区随之消失，引用要一起清掉，否则会留着个游离节点
      this.qaOpen = null;
      if (!this.todos.length) {
        const empty = document.createElement('div');
        empty.className = 'empty-state';
        empty.textContent = '还没有待办。贴一条通知，我们从第一张对照卡开始。';
        list.append(empty);
        $('#catchupSummary').textContent = '零待办、零预置历史，是正常的新朋友状态。';
        this.updateBubbleSummary();
        return;
      }
      for (const todo of this.todos) {
        const row = document.createElement('div');
        row.className = `todo-item ${todo.status === 'done' ? 'done' : ''}`;
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = todo.status === 'done';
        checkbox.setAttribute('aria-label', `完成 ${todo.title}`);
        checkbox.addEventListener('change', async () => {
          await this.api.patch(`/todos/${todo.id}`, { status: checkbox.checked ? 'done' : 'pending' });
          await Promise.all([this.loadTodos(), this.loadGrowth()]);
        });
        const body = document.createElement('div');
        const title = document.createElement('strong');
        title.textContent = todo.title;
        const detail = document.createElement('small');
        const deadline = todo.deadline ? new Date(todo.deadline).toLocaleString('zh-CN') : '无截止时间';
        detail.textContent = `${deadline}${todo.gap ? ` · 还差 ${todo.gap}` : ''}`;
        body.append(title, detail);
        // 阶段 9：每条待办一个「问」入口（只读问答，不写库、不改状态）
        const actions = document.createElement('div');
        actions.className = 'todo-actions';
        const ask = document.createElement('button');
        ask.type = 'button';
        ask.className = 'secondary qa-toggle';
        ask.textContent = '问';
        ask.setAttribute('aria-label', `问一句：${todo.title}`);
        ask.addEventListener('click', () => this.toggleTodoQa(row, todo));
        const remove = document.createElement('button');
        remove.textContent = '删';
        remove.setAttribute('aria-label', `删除 ${todo.title}`);
        remove.addEventListener('click', async () => { await this.api.delete(`/todos/${todo.id}`); await this.loadTodos(); });
        actions.append(ask, remove);
        row.append(checkbox, body, actions);
        list.append(row);
      }
      const pending = this.todos.filter((todo) => todo.status !== 'done').length;
      $('#catchupSummary').textContent = pending ? `还有 ${pending} 条待完成，奶龙会按截止时间提醒。` : '这一批都完成了，做得很好。';
      this.updateBubbleSummary();
    }

    // ---- 阶段 9：待办内一问一答（只聊当前这一条，不写库） ----
    toggleTodoQa(row, todo) {
      if (this.qaOpen) {
        const sameRow = this.qaOpen.dataset.todoId === todo.id;
        this.qaOpen.remove();
        this.qaOpen = null;
        if (sameRow) return;
      }
      const panel = document.createElement('div');
      panel.className = 'todo-qa';
      panel.dataset.todoId = todo.id;
      const input = document.createElement('input');
      input.className = 'qa-input';
      input.type = 'text';
      input.maxLength = 200;
      input.placeholder = '问一句，比如「这条还差什么」';
      input.setAttribute('aria-label', `关于「${todo.title}」的提问`);
      const send = document.createElement('button');
      send.type = 'button';
      send.className = 'secondary qa-send';
      send.textContent = '问';
      const answer = document.createElement('div');
      answer.className = 'qa-answer';
      answer.setAttribute('role', 'status');
      const submit = () => this.askTodo(todo, panel);
      send.addEventListener('click', submit);
      input.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        submit();
      });
      panel.append(input, send, answer);
      row.after(panel);
      this.qaOpen = panel;
      input.focus();
    }

    async askTodo(todo, panel) {
      const input = panel.querySelector('.qa-input');
      const send = panel.querySelector('.qa-send');
      const answer = panel.querySelector('.qa-answer');
      const question = (input.value || '').trim();
      if (!question) return;            // 空问题不发请求
      send.disabled = true;
      answer.classList.remove('degraded', 'error');
      answer.textContent = '正在问…';
      try {
        const result = await this.api.post('/task/qa', { todo_id: todo.id, question });
        const content = (result && result.content) || '';
        const degraded = Boolean(result && (result.degraded || result.type === 'degrade'));
        if (degraded) {
          // 降级必须看得见：灰底 + 明确的「模型没干活」口径，不许当成功
          answer.classList.add('degraded');
          const head = content.includes('模型暂不可用') ? '' : '模型暂不可用，以下是规则结果：';
          answer.textContent = `${head}${content || '当前任务还差的信息还没读到'}（规则兜底）`;
        } else if (result && result.type === 'done') {
          answer.textContent = content || '（模型这次没给出内容，再问一次试试）';
          const suggestions = Array.isArray(result.suggestions) ? result.suggestions.filter(Boolean) : [];
          if (suggestions.length) {
            const chips = document.createElement('div');
            chips.className = 'qa-chips';
            suggestions.forEach((text) => {
              const chip = document.createElement('span');
              chip.className = 'qa-chip';
              chip.textContent = text;
              chips.append(chip);
            });
            answer.append(chips);
          }
        } else {
          // type === 'error'（任务不存在等）：红字说明，不弹 alert
          answer.classList.add('error');
          answer.textContent = content || '未找到关联任务，请核对任务 ID';
        }
      } catch (error) {
        answer.classList.add('error');
        answer.textContent = `问不成：${error.message}`;
      } finally {
        send.disabled = false;
      }
    }

    async parseNotice() {
      const text = $('#noticeText').value.trim();
      if (!text) { this.toast('先贴一条通知吧'); return; }
      this.showView('notice');
      $('#parseNotice').disabled = true;
      $('#parseStatus').textContent = '正在按确定性步骤拆解…';
      const token = ++this.decisionToken;
      const pieces = text.split(/[。；;\n]/).map((part) => part.trim()).filter(Boolean);
      const stages = [
        `${Math.max(1, pieces.length)} 件事，我一件件理清，不着急。`,
        '先把真正要交的东西挑出来，再看时间。',
        this.todos.length ? `手上还有 ${this.todos.length} 件没做完，一起排好顺序。` : '清单还是空的，正好从头排起。',
        /(周|月|日|号|截止|前|\d{1,2}[:/])/.test(text) ? '截止时间我记下了，会提前提醒你。' : '这条没写截止时间，我先把缺口标出来。'
      ];
      const parsePromise = this.api.post('/notice/parse', { notice_text: text, use_v4_pro: false });
      this.renderTodoStrip(pieces);
      for (const stage of stages) {
        if (token !== this.decisionToken) return;
        this.setBubble(stage);
        await wait(260);
      }
      try {
        const result = await parsePromise;
        this.checklist = result.data.items || [];
        this.deadlineHint = result.data.deadline_hint || null;
        this.renderChecklist();
        this.renderTodoStrip(this.checklist.map((item) => item.text));
        this.machine.transition('checklist');
        const degraded = Boolean(result.degraded);
        this.setBubble('这几件事请你过一眼，缺的东西我都标出来了。', degraded ? '（规则版，未调模型）' : '', degraded);
        $('#parseStatus').textContent = degraded ? `规则版已完成${result.degrade_reason ? `：${result.degrade_reason}` : ''}` : '模型抽取已完成';
        if (degraded && !this.keyConfigured && !this.keyPromptDismissed) $('#keyPrompt').hidden = false;
      } catch (error) {
        this.checklist = pieces.map((piece) => ({ text: piece, category: 'deliverable', matched: false }));
        this.deadlineHint = null;
        this.renderChecklist();
        this.renderTodoStrip(this.checklist.map((item) => item.text));
        this.machine.transition('checklist');
        this.setBubble('本地服务暂时没回应，我先按页面规则替你理好了，随时可以再试。', '（规则版，未调模型）', true);
        $('#parseStatus').textContent = error.message;
      } finally {
        $('#parseNotice').disabled = false;
      }
    }

    renderChecklist() {
      const container = $('#checklist');
      container.replaceChildren();
      this.checklist.forEach((item, index) => {
        const card = document.createElement('div');
        card.className = 'check-card';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.id = `check-${index}`;
        checkbox.checked = Boolean(item.matched);
        checkbox.title = '勾选表示这条已经完成；未勾选项会落入待办';
        const body = document.createElement('div');
        const category = document.createElement('span');
        category.className = 'category';
        category.textContent = item.category;
        const text = document.createElement('p');
        text.textContent = item.text;
        body.append(category, text);
        card.append(checkbox, body);
        container.append(card);
      });
      $('#confirmChecklist').disabled = this.checklist.length === 0;
    }

    async confirmChecklist() {
      if (!this.checklist.length) return;
      const items = this.checklist.map((item, index) => ({
        text: item.text, category: item.category, matched: $(`#check-${index}`).checked
      }));
      try {
        await this.api.post('/notice/confirm', { items, deadline_override: this.deadlineHint });
        await Promise.all([this.loadTodos(), this.loadGrowth()]);
        this.director.play('work', { ttl: 2600, source: 'checklist' });
        const pending = this.pendingTodos();
        this.setBubble(this.reminderLine(pending), this.nearestDeadline(pending));
        this.showView('todos');
      } catch (error) { this.toast(error.message); }
    }

    // ---- 阶段 9：一句话补待办（只解析、只回填，落库仍由用户点「添加」） ----
    async parseVoice() {
      const hint = $('#voiceHint');
      const text = $('#voiceText').value.trim();
      hint.classList.remove('error', 'degraded');
      if (!text) {
        hint.textContent = '先写一句话，比如「周五前交实验报告」。';
        return;
      }
      const button = $('#voiceParse');
      button.disabled = true;
      hint.textContent = '正在解析这句话…';
      try {
        const result = await this.api.post('/todo/from-voice', { text });
        const data = (result && result.data) || {};
        const title = typeof data.title === 'string' ? data.title.trim() : '';
        const confidence = Number.isFinite(data.confidence)
          ? `置信度 ${Math.round(data.confidence * 100)}%`
          : '置信度未知';
        // 回填，不落库：用户改完点「添加」才走 addTodo()
        if (title) $('#todoTitle').value = title;
        if (data.deadline) $('#todoDeadline').value = toLocalInput(data.deadline);
        if (result && result.degraded) {
          // 降级也要说清楚：这是规则解析的结果，不是模型给的
          const reason = result.degrade_reason ? `：${result.degrade_reason}` : '';
          hint.classList.add('degraded');
          hint.textContent = `规则解析（${confidence}）${reason} · 核对后再点「添加」，不会自动落库`;
        } else if (!title) {
          hint.classList.add('degraded');
          hint.textContent = `没解析出标题（${confidence}），把要做的事写具体点再试`;
        } else {
          hint.textContent = `已解析（${confidence}）· 核对后再点「添加」，不会自动落库`;
        }
      } catch (error) {
        // 失败不清空用户已输入的内容
        hint.classList.add('error');
        hint.textContent = `解析失败：${error.message}`;
      } finally {
        button.disabled = false;
      }
    }

    async addTodo() {
      const title = $('#todoTitle').value.trim();
      if (!title) return;
      await this.api.post('/todos', { title, deadline: $('#todoDeadline').value || null, gap: $('#todoGap').value, source: 'manual' });
      $('#todoTitle').value = '';
      $('#todoDeadline').value = '';
      $('#todoGap').value = '';
      await Promise.all([this.loadTodos(), this.loadGrowth()]);
    }

    async exportDocx() {
      if (!this.todos.length) { $('#exportResult').textContent = '还没有待办可导出。'; return; }
      $('#exportResult').textContent = '正在本机生成 docx…';
      this.director.play('work', { ttl: 2600, source: 'export' });
      try {
        const result = await this.api.post('/export/docx', { todo_ids: this.todos.map((todo) => todo.id), title: '待办交付清单' });
        const link = document.createElement('a');
        link.href = result.download_url;
        link.textContent = '下载待办交付清单.docx';
        link.download = '';
        $('#exportResult').replaceChildren(link);
      } catch (error) { $('#exportResult').textContent = error.message; }
    }

    async loadKeyStatus() {
      const status = await this.api.get('/key/status');
      this.keyConfigured = Boolean(status.configured);
      $('#keyStatus').textContent = this.keyConfigured ? `已配置 · 尾四位 ${status.masked_tail}` : '未配置';
      $('#keyDetail').textContent = this.keyConfigured ? '只显示脱敏尾四位，可修改、删除或检查连通性。' : '自动档会静默使用规则版；首次打开不会索要 Key。';
      if (this.keyConfigured) $('#keyPrompt').hidden = true;
    }

    async changeKey() {
      if (!this.shell) { this.toast('Key 配置仅在桌面壳内开放'); return; }
      $('#keyCheckResult').textContent = '正在打开输入框…页面不会接收明文。';
      try {
        const result = await this.shell.changeKey();
        await this.loadKeyStatus();
        // 取消要如实说取消：之前不管有没有拿到 Key 都回「配置已更新」，等于骗人
        $('#keyCheckResult').textContent = result && result.saved
          ? '已加密保存到本机，本地服务已用新 Key 重启。'
          : '已取消，Key 未修改。';
      } catch (error) { $('#keyCheckResult').textContent = `保存失败：${error.message}`; }
    }

    async deleteKey() {
      $('#keyCheckResult').textContent = '正在删除本机加密份并重启本地服务…';
      if (this.shell) await this.shell.deleteKey();
      else await this.api.delete('/key');
      this.keyConfigured = false;
      $('#keyStatus').textContent = '未配置';
      $('#keyDetail').textContent = '已删除，界面立刻回到规则版。';
      $('#keyCheckResult').textContent = '已清除本机加密份与 Python 进程注入，本地服务已重启。';
    }

    async checkKey() {
      $('#keyCheckResult').textContent = '正在检查…';
      try {
        const result = this.shell ? await this.shell.checkKey() : await this.api.post('/key/check');
        $('#keyCheckResult').textContent = result.detail || (result.ok ? '连通正常' : '未通过');
      } catch (error) { $('#keyCheckResult').textContent = error.message; }
    }

    async loadModelSwitch() {
      const result = await this.api.get('/settings/model-switch');
      $('#modelSwitch').value = result.state;
    }

    async setModelSwitch(state) {
      await this.api.post('/settings/model-switch', { state });
      this.toast(state === 'off' ? '已关闭模型，主流程继续走规则版' : '模型开关已更新');
    }

    applyDesktopSettings(settings) {
      $('#alwaysOnTop').checked = settings.alwaysOnTop;
      $('#muted').checked = settings.muted;
      $('#nightTheme').checked = settings.theme === 'night';
      this.setTheme(settings.theme, false);
    }

    setTheme(theme, persist = true) {
      document.documentElement.dataset.theme = theme === 'night' ? 'night' : 'day';
      $('#nightTheme').checked = theme === 'night';
      if (persist && this.shell) this.shell.setTheme(theme);
    }

  }

  const app = new DeskpetApp();
  app.init();
})();
