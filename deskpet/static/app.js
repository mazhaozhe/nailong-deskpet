(() => {
  'use strict';

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  class Api {
    constructor() {
      const key = 'deskpet_uid';
      this.uid = localStorage.getItem(key);
      if (!this.uid) {
        this.uid = crypto.randomUUID();
        localStorage.setItem(key, this.uid);
      }
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
        : ['collapsed', 'open', 'notice', 'todos', 'game', 'settings', 'about', 'checklist'];
      if (!allowed.includes(next)) return false;
      this.state = next;
      return true;
    }
  }

  class PetAnimator {
    constructor(canvas, shell) {
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d', { willReadFrequently: true });
      this.shell = shell;
      this.manifest = null;
      this.animations = new Map();
      this.images = new Map();
      this.current = null;
      this.currentKey = '';
      this.direction = 1;
      this.startedAt = 0;
      this.lastAt = 0;
      this.behaviorDue = 0;
      this.completed = false;
      this.dragging = false;
      this.hidden = document.hidden;
      this.moveElapsed = 0;
      this.movePending = false;
      this.behaviorIndex = 0;
      this.behaviors = ['idle2', 'sit', 'idle3', 'stroll', 'idle4', 'sleep', 'idle', 'walk', 'work'];
      document.addEventListener('visibilitychange', () => {
        this.hidden = document.hidden;
        this.lastAt = 0;
        if (!this.hidden) this.behaviorDue = performance.now() + this.idleDelay();
      });
    }

    idleDelay() {
      const [min, max] = this.manifest ? this.manifest.meta.idleMs : [25000, 35000];
      return min + Math.random() * (max - min);
    }

    async load() {
      const response = await fetch('/static/nailong/manifest.json');
      if (!response.ok) throw new Error('奶龙动作表读取失败');
      this.manifest = await response.json();
      for (const animation of this.manifest.animations) this.animations.set(animation.key, animation);
      const names = [...new Set(this.manifest.animations.flatMap((animation) => animation.frames.map((frame) => frame.name)))];
      await Promise.all(names.map((name) => new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => { this.images.set(name, image); resolve(); };
        image.onerror = () => reject(new Error(`奶龙帧缺失：${name}`));
        image.src = `/static/nailong/frames/${name}.png`;
      })));
      this.canvas.dataset.animations = String(this.animations.size);
      this.canvas.dataset.frames = String(names.length);
      this.switchTo('idle', true);
      requestAnimationFrame((time) => this.tick(time));
    }

    switchTo(key, force = false) {
      const next = this.animations.get(key);
      if (!next || (!force && this.dragging && key !== 'run')) return false;
      this.currentKey = key;
      this.current = next;
      this.startedAt = performance.now();
      this.completed = false;
      this.direction = next.facing === 'left' ? -1 : 1;
      this.moveElapsed = 0;
      const mobile = next.move > 0;
      this.behaviorDue = performance.now() + (mobile ? 9000 : this.idleDelay());
      this.canvas.dataset.action = key;
      return true;
    }

    react() {
      if (this.dragging) return;
      const sequence = ['wave', 'laugh', 'shy'];
      const next = sequence[(sequence.indexOf(this.currentKey) + 1) % sequence.length];
      this.switchTo(next);
    }

    work() { if (!this.dragging) this.switchTo('work'); }

    setDragging(value) {
      this.dragging = value;
      if (value) this.switchTo('run', true);
      else this.switchTo('shy', true);
    }

    chooseBehavior(now) {
      const key = this.behaviors[this.behaviorIndex % this.behaviors.length];
      this.behaviorIndex += 1;
      this.switchTo(key);
      if (key === 'walk' || key === 'stroll') this.behaviorDue = now + 9000;
    }

    hitTest(event) {
      if (!this.current) return false;
      const rect = this.canvas.getBoundingClientRect();
      const x = Math.floor((event.clientX - rect.left) * this.canvas.width / rect.width);
      const y = Math.floor((event.clientY - rect.top) * this.canvas.height / rect.height);
      if (x < 0 || y < 0 || x >= this.canvas.width || y >= this.canvas.height) return false;
      try { return this.ctx.getImageData(x, y, 1, 1).data[3] > 16; } catch { return false; }
    }

    async moveWindow(delta) {
      if (!this.shell || this.movePending || delta === 0) return;
      this.movePending = true;
      try {
        const bounds = await this.shell.moveWindow({ dx: delta, dy: 0 });
        if (bounds && ((bounds.hitLeft && this.direction < 0) || (bounds.hitRight && this.direction > 0))) {
          this.direction *= -1;
        }
      } catch {} finally { this.movePending = false; }
    }

    draw() {
      if (!this.current) return;
      const frameIndex = this.frameIndex || 0;
      const frame = this.current.frames[Math.min(frameIndex, this.current.frames.length - 1)];
      const image = this.images.get(frame.name);
      if (!image) return;
      const width = this.manifest.meta.cellW;
      const height = this.manifest.meta.cellH;
      this.ctx.clearRect(0, 0, width, height);
      this.ctx.save();
      const baseDirection = this.current.facing === 'left' ? -1 : 1;
      if (this.direction !== baseDirection && this.current.facing !== 'front') {
        this.ctx.translate(width, 0);
        this.ctx.scale(-1, 1);
      }
      // 帧的 ox/oy/w/h 来自 manifest；源帧地面线已对齐到 y=203。
      this.ctx.drawImage(image, this.current.ox, frame.oy, frame.w, frame.h);
      this.ctx.restore();
    }

    tick(now) {
      requestAnimationFrame((time) => this.tick(time));
      if (this.hidden || !this.current) { this.lastAt = now; return; }
      const delta = this.lastAt ? Math.min((now - this.lastAt) / 1000, 0.1) : 0;
      this.lastAt = now;
      const animation = this.current;
      const elapsed = Math.max(0, (now - this.startedAt) / 1000);
      const rawFrame = Math.floor(elapsed * animation.fps);
      const frameCount = animation.frames.length;
      if (animation.loop) {
        this.frameIndex = rawFrame % frameCount;
      } else {
        const total = frameCount * Math.max(1, animation.repeat || 1);
        this.frameIndex = Math.min(rawFrame, frameCount - 1);
        if (rawFrame >= total) {
          this.frameIndex = animation.hold ? frameCount - 1 : frameCount - 1;
          if (!animation.hold && !this.completed) {
            this.completed = true;
            this.switchTo('idle', true);
          }
        }
      }
      if (!this.dragging && now >= this.behaviorDue && (animation.loop || animation.hold)) this.chooseBehavior(now);
      if (!this.dragging && animation.move > 0 && this.shell) {
        this.moveElapsed += delta;
        if (this.moveElapsed >= 0.08) {
          const distance = animation.move * this.moveElapsed * this.direction;
          this.moveElapsed = 0;
          this.moveWindow(distance);
        }
      }
      this.draw();
    }
  }

  class DeskpetApp {
    constructor() {
      this.api = new Api();
      this.shell = window.deskpetShell || null;
      this.machine = new UiStateMachine();
      this.pet = new PetAnimator($('#petCanvas'), this.shell);
      this.todos = [];
      this.growth = null;
      this.checklist = [];
      this.deadlineHint = null;
      this.keyConfigured = false;
      this.keyPromptDismissed = false;
      this.decisionToken = 0;
      this.drag = null;
      this.suppressClick = false;
      this.toastTimer = null;
      this.game = null;
      this.gameTimer = null;
      this.gamePaused = false;
    }

    async init() {
      if (document.body.classList.contains('desktop') === false && new URLSearchParams(location.search).has('desktop')) {
        document.body.classList.add('desktop');
      }
      this.bindEvents();
      if (this.shell) {
        this.shell.identify(this.api.uid);
        this.shell.onCommand((command) => { if (command === 'open') this.openPanel(); });
        this.shell.onSettings((settings) => this.applyDesktopSettings(settings));
        this.shell.getSettings().then((settings) => this.applyDesktopSettings(settings)).catch(() => {});
      }
      const jobs = [
        this.pet.load().catch((error) => this.setBubble(error.message)),
        this.api.post('/growth/simulate/clear').then(() => this.loadGrowth()).catch(() => this.setBubble('本地成长数据暂时读不到，点我仍可打开清单。')),
        this.loadTodos().catch(() => {}),
        this.loadNotices().catch(() => this.toast('样例通知暂时不可用')),
        this.loadKeyStatus().catch(() => {}),
        this.loadModelSwitch().catch(() => {})
      ];
      await Promise.allSettled(jobs);
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
      $('#startGame').addEventListener('click', () => this.startGame());
      $('#finishGame').addEventListener('click', () => this.finishGame('manual'));
      $('#commentaryEnabled').addEventListener('change', () => this.updateCommentaryButton());
      $('#requestCommentary').addEventListener('click', () => this.requestCommentary());
      $$('[data-external]').forEach((link) => link.addEventListener('click', (event) => {
        if (this.shell) { event.preventDefault(); this.shell.openExternal(link.href); }
      }));
      document.addEventListener('pointermove', (event) => this.updateMousePassthrough(event));
      const canvas = $('#petCanvas');
      canvas.addEventListener('pointerdown', (event) => this.pointerDown(event));
      canvas.addEventListener('pointermove', (event) => this.pointerMove(event));
      canvas.addEventListener('pointerup', (event) => this.pointerUp(event));
      canvas.addEventListener('pointercancel', (event) => this.pointerUp(event));
      canvas.addEventListener('click', () => {
        if (this.suppressClick) return;
        this.activatePet();
      });
      canvas.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') this.activatePet(); });
      canvas.addEventListener('contextmenu', (event) => { event.preventDefault(); if (this.shell) this.shell.contextMenu(); });
      document.addEventListener('visibilitychange', () => this.toggleGamePause(document.hidden));
    }

    updateMousePassthrough(event) {
      if (!this.shell) return;
      const interactive = event.target.closest('.panel,.bubble') || (event.target === $('#petCanvas') && this.pet.hitTest(event));
      this.shell.setMousePassthrough(!interactive);
    }

    pointerDown(event) {
      if (event.button !== 0 || !this.pet.hitTest(event)) return;
      this.drag = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, moved: false };
      try { event.currentTarget.setPointerCapture(event.pointerId); } catch {}
      if (this.shell) this.shell.dragStart();
    }

    pointerMove(event) {
      if (!this.drag || this.drag.pointerId !== event.pointerId) return;
      if (Math.hypot(event.clientX - this.drag.startX, event.clientY - this.drag.startY) > 4 && !this.drag.moved) {
        this.drag.moved = true;
        this.pet.setDragging(true);
      }
      if (this.drag.moved && this.shell) this.shell.dragMove();
    }

    pointerUp(event) {
      if (!this.drag || this.drag.pointerId !== event.pointerId) return;
      const moved = this.drag.moved;
      this.drag = null;
      this.suppressClick = true;
      setTimeout(() => { this.suppressClick = false; }, 50);
      if (moved) {
        if (this.shell) this.shell.dragEnd();
        this.pet.setDragging(false);
      } else {
        this.activatePet();
      }
    }

    activatePet() {
      this.pet.react();
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
      this.pet.switchTo('idle');
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
      if (displayDay === 1 && this.todos.length === 0) this.setBubble('今天是认识你的第 1 天，贴一条通知给我试试。');
    }

    async simulate(day) {
      const result = await this.api.post('/growth/simulate', { target_day: day });
      this.renderGrowth(result.data);
      this.pet.switchTo(day >= 30 ? 'laugh' : 'shy');
      this.setBubble(`现在只是展示第 ${day} 天的样子，刷新就回真实天数。`, '模拟展示，不写历史');
    }

    async clearSimulation() {
      const result = await this.api.post('/growth/simulate/clear');
      this.renderGrowth(result.data);
      this.pet.switchTo('idle');
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
      $('#todoCount').textContent = String(this.todos.length);
      const list = $('#todoList');
      list.replaceChildren();
      if (!this.todos.length) {
        const empty = document.createElement('div');
        empty.className = 'empty-state';
        empty.textContent = '还没有待办。贴一条通知，我们从第一张对照卡开始。';
        list.append(empty);
        $('#catchupSummary').textContent = '零待办、零预置历史，是正常的新朋友状态。';
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
        const remove = document.createElement('button');
        remove.textContent = '删';
        remove.setAttribute('aria-label', `删除 ${todo.title}`);
        remove.addEventListener('click', async () => { await this.api.delete(`/todos/${todo.id}`); await this.loadTodos(); });
        row.append(checkbox, body, remove);
        list.append(row);
      }
      const pending = this.todos.filter((todo) => todo.status !== 'done').length;
      $('#catchupSummary').textContent = pending ? `还有 ${pending} 条待完成，奶龙会按截止时间提醒。` : '这一批都完成了，做得很好。';
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
        `读到 ${Math.max(1, pieces.length)} 件事，先逐条看清。`,
        `把通知拆成 ${Math.max(1, pieces.length)} 张对照卡。`,
        `查到 ${this.todos.length} 条既有待办，避免混在一起。`,
        /(周|月|日|号|截止|前|\d{1,2}[:/])/.test(text) ? '时间线索已标出，接下来报还缺的内容。' : '没读到明确截止时间，先把缺口标出来。'
      ];
      $('#decisionChain').replaceChildren();
      const parsePromise = this.api.post('/notice/parse', { notice_text: text, use_v4_pro: false });
      for (const stage of stages) {
        if (token !== this.decisionToken) return;
        this.setBubble(stage);
        const item = document.createElement('li');
        item.textContent = stage.split('，')[0];
        $('#decisionChain').append(item);
        await wait(260);
      }
      try {
        const result = await parsePromise;
        this.checklist = result.data.items || [];
        this.deadlineHint = result.data.deadline_hint || null;
        this.renderChecklist();
        this.machine.transition('checklist');
        const degraded = Boolean(result.degraded);
        this.setBubble(`拆好了，共 ${this.checklist.length} 张对照卡；有缺口的地方逐项核对。`, degraded ? '（规则版，未调模型）' : '', degraded);
        $('#parseStatus').textContent = degraded ? `规则版已完成${result.degrade_reason ? `：${result.degrade_reason}` : ''}` : '模型抽取已完成';
        if (degraded && !this.keyConfigured && !this.keyPromptDismissed) $('#keyPrompt').hidden = false;
      } catch (error) {
        this.checklist = pieces.map((piece) => ({ text: piece, category: 'deliverable', matched: false }));
        this.deadlineHint = null;
        this.renderChecklist();
        this.machine.transition('checklist');
        this.setBubble('本地服务暂时没回应，我先用页面规则拆好，稍后可再试。', '（规则版，未调模型）', true);
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
        const result = await this.api.post('/notice/confirm', { items, deadline_override: this.deadlineHint });
        await Promise.all([this.loadTodos(), this.loadGrowth()]);
        this.pet.work();
        this.setBubble(`把 ${result.created.length} 条未完成项放进清单了。`);
        this.showView('todos');
      } catch (error) { this.toast(error.message); }
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
      this.pet.work();
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
      $('#keyCheckResult').textContent = '请在系统密码框中输入；页面不会接收明文。';
      try {
        await this.shell.changeKey();
        await this.loadKeyStatus();
        $('#keyCheckResult').textContent = '配置已更新，本地服务已重新接线。';
      } catch (error) { $('#keyCheckResult').textContent = error.message; }
    }

    async deleteKey() {
      if (this.shell) await this.shell.deleteKey();
      else await this.api.delete('/key');
      this.keyConfigured = false;
      $('#keyStatus').textContent = '未配置';
      $('#keyDetail').textContent = '已删除，界面立刻回到规则版。';
      $('#keyCheckResult').textContent = '已清除本机加密份与 Python 进程注入。';
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

    startGame() {
      this.stopGameTimer();
      this.api.post('/game/start', { game_id: 'mini-snake', brain_level: 2 }).then((result) => {
        this.game = {
          sessionId: result.session_id,
          snake: [{ x: 6, y: 6 }],
          food: { x: 9, y: 6 },
          direction: { x: 1, y: 0 },
          steps: 0,
          score: 0,
          strategy: result.initial_strategy ? result.initial_strategy.strategy_name : 'balanced',
          reviewed: false
        };
        $('#finishGame').disabled = false;
        $('#requestCommentary').disabled = true;
        $('#gameLog').textContent = `开局成功。规则策略：${this.game.strategy}；解说保持${$('#commentaryEnabled').checked ? '开启但需手动点' : '关闭'}。`;
        this.drawGame();
        this.gameTimer = setInterval(() => this.stepGame(), 180);
      }).catch((error) => { $('#gameLog').textContent = error.message; });
    }

    stopGameTimer() {
      if (this.gameTimer) clearInterval(this.gameTimer);
      this.gameTimer = null;
    }

    toggleGamePause(hidden) {
      this.gamePaused = hidden;
      if (!this.game) return;
      if (hidden) this.stopGameTimer();
      else if (!this.game.reviewed && !this.gameTimer) this.gameTimer = setInterval(() => this.stepGame(), 180);
    }

    stepGame() {
      if (!this.game || this.gamePaused) return;
      const game = this.game;
      const head = game.snake[0];
      const dx = game.food.x - head.x;
      const dy = game.food.y - head.y;
      let direction = Math.abs(dx) > Math.abs(dy) ? { x: Math.sign(dx), y: 0 } : { x: 0, y: Math.sign(dy) };
      if (direction.x === -game.direction.x && direction.y === -game.direction.y) direction = game.direction;
      game.direction = direction;
      const next = { x: head.x + direction.x, y: head.y + direction.y };
      game.steps += 1;
      if (next.x < 0 || next.y < 0 || next.x >= 12 || next.y >= 12 || game.snake.some((part) => part.x === next.x && part.y === next.y)) {
        this.finishGame('collision');
        return;
      }
      game.snake.unshift(next);
      if (next.x === game.food.x && next.y === game.food.y) {
        game.score += 1;
        game.food = { x: (game.food.x * 7 + 3) % 12, y: (game.food.y * 5 + 4) % 12 };
      } else game.snake.pop();
      if (game.steps >= 120) { this.finishGame('completed'); return; }
      this.drawGame();
    }

    drawGame() {
      const canvas = $('#gameCanvas');
      const ctx = canvas.getContext('2d');
      const cell = 20;
      ctx.fillStyle = '#29352e';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      if (!this.game) return;
      ctx.fillStyle = '#efb76e';
      ctx.fillRect(this.game.food.x * cell + 5, this.game.food.y * cell + 5, 10, 10);
      this.game.snake.forEach((part, index) => {
        ctx.fillStyle = index === 0 ? '#f8e0a4' : '#87ad7c';
        ctx.fillRect(part.x * cell + 2, part.y * cell + 2, 16, 16);
      });
    }

    async finishGame(reason) {
      if (!this.game || this.game.reviewed) return;
      const game = this.game;
      this.stopGameTimer();
      $('#finishGame').disabled = true;
      $('#gameLog').textContent = '本局结束，优先生成复盘…';
      try {
        const result = await this.api.post('/game/review', {
          session_id: game.sessionId,
          final_state: { game_id: 'mini-snake', result: reason, score: game.score, steps: game.steps },
          strategy_history: [game.strategy]
        });
        game.reviewed = true;
        $('#gameLog').textContent = `复盘：${result.data.takeaway}\n下次：${result.data.next_change || '保持规则策略，再试一局。'}`;
        this.updateCommentaryButton();
      } catch (error) { $('#gameLog').textContent = `复盘暂时不可用：${error.message}`; }
    }

    updateCommentaryButton() {
      $('#requestCommentary').disabled = !($('#commentaryEnabled').checked && this.game && this.game.reviewed);
    }

    async requestCommentary() {
      if (!this.game || !this.game.reviewed || !$('#commentaryEnabled').checked) return;
      $('#requestCommentary').disabled = true;
      try {
        const result = await this.api.post('/game/commentary', {
          session_id: this.game.sessionId,
          state_snapshot: { game_id: 'mini-snake', result: 'reviewed', score: this.game.score },
          strategy_name: this.game.strategy
        });
        this.setBubble(result.data.line, result.degraded ? '（规则版，未调模型）' : '', result.degraded);
        $('#gameLog').textContent += `\n解说：${result.data.line}`;
      } catch (error) { $('#gameLog').textContent = error.message; }
      finally { this.updateCommentaryButton(); }
    }
  }

  const app = new DeskpetApp();
  app.init();
})();
