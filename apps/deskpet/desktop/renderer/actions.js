// 动作调度层（会话 5）：把「谁想让桌宠做什么」收成一条带优先级的通道。
// 设计参考：nigaray703-ops/nigar-kiz-desktop-pet 把「交互决策」从渲染器里拆出来
// （interaction-controller.js）；JOhnsonKC201/pixelpets 用「主进程调度、渲染层只画」
// 的分工；oahc09/Clyde 的「事件 → 动作」映射表 + 空闲降级链。
// 本文件只做决策：帧怎么放、像素怎么画仍然归 pet.js，不重写那两个状态机。
'use strict';

// 优先级：数字大者胜。高优先级占用期间，低优先级请求进队列而不是抢帧
// （抢帧的现场表现就是「动作来回闪」）。
const PRIORITY = Object.freeze({
  IDLE: 0,
  SLEEP: 5,
  MOOD: 20,        // 闲聊 mood 映射出来的表情（shy / sit / idle）
  REACT: 30,       // 点一下的挥手
  CELEBRATE: 40,
  WORK: 50,
  THINKING: 60,
  LAND: 90,        // 松手落地：比 thinking 高，落地的第一下不能被别的请求吃掉
  LIFTED: 100      // 被提起来：最高，只能显式 release
});

// 动作键表（可枚举，验收可直接抽查）：
//  - anim  ：manifest.json 里真实存在的动画 key（本轮不新增 png）；
//  - pose  ：写到 #petScene[data-pose] 上的姿态，纯 CSS 变换（位移/旋转/弹跳/阴影）；
//  - ttl   ：毫秒；0 表示只能靠 release() 结束（lifted 这种主动保持态）。
const ACTIONS = Object.freeze({
  idle: { anim: 'idle', priority: PRIORITY.IDLE, ttl: 0, pose: '' },
  sleep: { anim: 'sleep', priority: PRIORITY.SLEEP, ttl: 30000, pose: '' },
  shy: { anim: 'shy', priority: PRIORITY.MOOD, ttl: 2400, pose: 'pose-shy' },
  sit: { anim: 'sit', priority: PRIORITY.MOOD, ttl: 4000, pose: '' },
  wave: { anim: 'wave', priority: PRIORITY.REACT, ttl: 1800, pose: 'pose-wave' },
  celebrate: { anim: 'laugh', priority: PRIORITY.CELEBRATE, ttl: 2400, pose: 'pose-celebrate' },
  work: { anim: 'work', priority: PRIORITY.WORK, ttl: 3200, pose: 'pose-work' },
  thinking: { anim: 'sit', priority: PRIORITY.THINKING, ttl: 12000, pose: 'pose-thinking' },
  land: { anim: 'idle2', priority: PRIORITY.LAND, ttl: 800, pose: 'pose-land' },
  lifted: { anim: 'shy', priority: PRIORITY.LIFTED, ttl: 0, pose: 'pose-lifted' }
});

const ACTION_KEYS = Object.freeze(Object.keys(ACTIONS));

class ActionDirector {
  /**
   * @param {object} options
   * @param {object} options.pet      PetController（只需 switchTo / setHold）
   * @param {object} [options.scene]  写 data-action / data-pose 的容器（#petScene）
   * @param {function} [options.onChange] 动作切换回调（画任务小画面等）
   * @param {function} [options.now]  取当前时间（测试可注入）
   */
  constructor({ pet, scene = null, onChange = null, now = null } = {}) {
    this.pet = pet;
    this.scene = scene;
    this.onChange = onChange;
    this.now = now || (() => (typeof performance !== 'undefined' ? performance.now() : Date.now()));
    this.active = null;   // {key, priority, source, expiresAt}
    this.pending = null;  // 低优先级排队中的请求（同优先级/更高优先级直接抢占，不进队）
    this.history = [];    // 最近 20 次切换，验收时可读
    this.apply('idle', { priority: PRIORITY.IDLE, ttl: 0, source: 'init' });
  }

  static get ACTION_KEYS() { return ACTION_KEYS; }
  static get ACTIONS() { return ACTIONS; }
  static get PRIORITY() { return PRIORITY; }

  describe() {
    return ACTION_KEYS.map((key) => ({
      key,
      anim: ACTIONS[key].anim,
      priority: ACTIONS[key].priority,
      ttl: ACTIONS[key].ttl,
      pose: ACTIONS[key].pose
    }));
  }

  current() {
    return this.active ? this.active.key : null;
  }

  /**
   * 统一入口：所有动作切换都必须经过这里，禁止各处直接调 pet.switchTo()。
   * @returns {boolean} 是否当场生效（进队或丢弃都返回 false）
   */
  play(key, { priority = null, ttl = null, source = 'app' } = {}) {
    const action = ACTIONS[key];
    if (!action) return false;
    const wanted = Number.isFinite(priority) ? priority : action.priority;
    const life = Number.isFinite(ttl) ? ttl : action.ttl;

    // 同一个动作重复请求：只续期，不重置帧（重置帧 = 视觉抖动）
    if (this.active && this.active.key === key) {
      this.active.expiresAt = life > 0 ? this.now() + life : 0;
      this.active.source = source;
      return true;
    }

    // 同源接管：同一个来源（比如拖拽）的后续动作可以直接顶掉自己占着的动作，
    // 否则「松手落地」这种同源收尾会被自己的 lifted 挡住，永远落不下来。
    const sameSource = Boolean(this.active) && this.active.source === source;
    if (this.active && wanted < this.active.priority && !sameSource) {
      // 高优先级占用中：低优先级只留一条（同级别里来的最后一条），不抢帧
      if (!this.pending || wanted >= this.pending.priority) {
        this.pending = { key, priority: wanted, ttl: life, source };
      }
      return false;
    }

    this.apply(key, { priority: wanted, ttl: life, source });
    return true;
  }

  /**
   * 显式释放某个来源占着的动作（如聊天请求结束）。
   * 只有当前动作确实来自该来源才回落，避免误释放别人的动作。
   */
  release(source) {
    let released = false;
    if (this.pending && this.pending.source === source) {
      this.pending = null;
      released = true;
    }
    if (!this.active || this.active.source !== source) return released;
    this.active = null;
    this.settle();
    return true;
  }

  /** 清空一切占用，回 idle（面板收起、页面重载等场景）。 */
  reset() {
    this.pending = null;
    this.active = null;
    this.apply('idle', { priority: PRIORITY.IDLE, ttl: 0, source: 'reset' });
  }

  /** 由渲染循环驱动（app.js 的 rAF 每帧调一次）。测试可手动喂时间戳。 */
  tick(now = null) {
    const at = Number.isFinite(now) ? now : this.now();
    if (!this.active || !this.active.expiresAt) return false;
    if (at < this.active.expiresAt) return false;
    this.active = null;
    this.settle();
    return true;
  }

  /** ttl 到期 / 被释放之后：有排队就接上，没有就回 idle。 */
  settle() {
    const next = this.pending;
    this.pending = null;
    if (next) {
      this.apply(next.key, { priority: next.priority, ttl: next.ttl, source: next.source });
      return;
    }
    this.apply('idle', { priority: PRIORITY.IDLE, ttl: 0, source: 'idle' });
  }

  apply(key, { priority, ttl, source }) {
    const action = ACTIONS[key];
    const expiresAt = ttl > 0 ? this.now() + ttl : 0;
    this.active = { key, priority, source, expiresAt };
    this.history.push({ key, priority, source, at: Math.round(this.now()) });
    if (this.history.length > 20) this.history.shift();
    if (this.pet) {
      // hold=true：由调度层决定何时结束，pet.js 不要自作主张回 idle / 轮播 idle
      if (typeof this.pet.setHold === 'function') this.pet.setHold(key !== 'idle');
      this.pet.switchTo(action.anim, true);
    }
    if (this.scene) {
      this.scene.dataset.action = key;
      this.scene.dataset.priority = String(priority);
      if (action.pose) this.scene.dataset.pose = action.pose;
      else delete this.scene.dataset.pose;
    }
    if (typeof this.onChange === 'function') this.onChange(key, action);
  }
}

if (typeof module !== 'undefined' && module.exports) module.exports = { ActionDirector, ACTIONS, PRIORITY };
if (typeof window !== 'undefined') window.ActionDirector = ActionDirector;
