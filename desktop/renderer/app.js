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

  class DeskpetApp {
    constructor() {
      this.api = new Api();
      this.shell = window.deskpetShell || null;
      this.machine = new UiStateMachine();
      this.pet = new PetController($('#petCanvas'), this.shell, () => this.activatePet());
      this.todos = [];
      this.growth = null;
      this.checklist = [];
      this.deadlineHint = null;
      this.keyConfigured = false;
      this.keyPromptDismissed = false;
      this.decisionToken = 0;
      this.toastTimer = null;
      this.game = null;
      this.gameTimer = null;
      this.gamePaused = false;
      this.gameStarting = false;
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
      document.addEventListener('visibilitychange', () => this.toggleGamePause(document.hidden));
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
      this.toggleGamePause(true);
      $('#panel').hidden = true;
      this.pet.switchTo('idle');
    }

    showView(name) {
      if (this.machine.state === 'collapsed') this.machine.transition('open');
      this.machine.transition(name);
      this.toggleGamePause(name !== 'game' || document.hidden);
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

    async startGame() {
      if (this.gameStarting) return;
      this.gameStarting = true;
      $('#startGame').disabled = true;
      $('#finishGame').disabled = true;
      $('#requestCommentary').disabled = true;
      this.stopGameTimer();
      if (this.game) this.game.ended = true;
      $('#gameLog').textContent = '正在启动本地规则游戏…';
      try {
        const result = await this.api.post('/game/start', { game_id: 'mini-snake', brain_level: 1 });
        this.game = {
          sessionId: result.session_id,
          snake: [{ x: 6, y: 6 }],
          food: { x: 9, y: 6 },
          direction: { x: 1, y: 0 },
          steps: 0,
          score: 0,
          strategy: 'greedy_food',
          ended: false,
          reviewed: false
        };
        $('#finishGame').disabled = false;
        $('#gameLog').textContent = '规则游戏已开始。开局不调用模型，结束后再生成复盘。';
        this.drawGame();
        if (!this.gamePaused) this.gameTimer = setInterval(() => this.stepGame(), 180);
      } catch (error) {
        $('#gameLog').textContent = `启动失败：${error.message}`;
      } finally {
        this.gameStarting = false;
        $('#startGame').disabled = false;
      }
    }

    stopGameTimer() {
      if (this.gameTimer) clearInterval(this.gameTimer);
      this.gameTimer = null;
    }

    toggleGamePause(hidden) {
      this.gamePaused = hidden || this.machine.state !== 'game';
      if (!this.game) return;
      if (this.gamePaused) this.stopGameTimer();
      else if (!this.game.ended && !this.game.reviewed && !this.gameStarting && !this.gameTimer) this.gameTimer = setInterval(() => this.stepGame(), 180);
    }

    stepGame() {
      if (!this.game || this.game.ended || this.gamePaused) return;
      const game = this.game;
      const head = game.snake[0];
      const directions = [game.direction, { x: 0, y: -1 }, { x: 0, y: 1 }, { x: -1, y: 0 }, { x: 1, y: 0 }];
      const choices = directions.filter((direction) => {
        if (direction.x === -game.direction.x && direction.y === -game.direction.y) return false;
        const x = head.x + direction.x;
        const y = head.y + direction.y;
        const eating = x === game.food.x && y === game.food.y;
        const body = eating ? game.snake : game.snake.slice(0, -1);
        return x >= 0 && y >= 0 && x < 12 && y < 12
          && !body.some((part) => part.x === x && part.y === y);
      });
      game.steps += 1;
      if (!choices.length) {
        this.finishGame('collision');
        return;
      }
      choices.sort((a, b) => {
        const distance = (direction) => Math.abs(head.x + direction.x - game.food.x)
          + Math.abs(head.y + direction.y - game.food.y);
        return distance(a) - distance(b);
      });
      game.direction = choices[0];
      const next = { x: head.x + game.direction.x, y: head.y + game.direction.y };
      const eating = next.x === game.food.x && next.y === game.food.y;
      if (!eating) game.snake.pop();
      game.snake.unshift(next);
      if (eating) {
        game.score += 1;
        const free = [];
        for (let y = 0; y < 12; y++) {
          for (let x = 0; x < 12; x++) {
            if (!game.snake.some((part) => part.x === x && part.y === y)) free.push({ x, y });
          }
        }
        if (!free.length) {
          this.drawGame();
          this.finishGame('completed');
          return;
        }
        game.food = free[(game.score * 17) % free.length];
      }
      this.drawGame();
      if (game.steps >= 120) this.finishGame('completed');
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
      if (!this.game || this.game.ended) return;
      const game = this.game;
      game.ended = true;
      this.stopGameTimer();
      $('#finishGame').disabled = true;
      $('#gameLog').textContent = `本局结束：${reason}，得分 ${game.score}，共 ${game.steps} 步。正在生成复盘…`;
      try {
        const result = await this.api.post('/game/review', {
          session_id: game.sessionId,
          final_state: { game_id: 'mini-snake', result: reason, score: game.score, steps: game.steps },
          strategy_history: [game.strategy]
        });
        if (this.game !== game) return;
        game.reviewed = true;
        $('#gameLog').textContent = `复盘：${result.data.takeaway}\n下次：${result.data.next_change || '保持规则策略，再试一局。'}`;
        this.updateCommentaryButton();
      } catch (error) {
        if (this.game === game) $('#gameLog').textContent = `本局得分 ${game.score}。复盘暂时不可用：${error.message}`;
      }
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
