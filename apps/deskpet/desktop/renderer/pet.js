// Pointer lifecycle adapted from d4x1/petex (Apache-2.0); see apps/deskpet/desktop/vendor/petex/SOURCE.md.
// Modified: basic pointer dragging only; running and autonomous movement are disabled.
'use strict';

class PetController {
  constructor(canvas, shell, activate) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.shell = shell;
    this.activate = activate;
    this.animations = new Map();
    this.images = new Map();
    this.current = null;
    this.frameIndex = -1;
    this.ignoreMouse = null;
    this.idleIndex = 0;
    // 会话 5：true = 当前动作由 ActionDirector 持有，本文件不再自动回 idle、
    // 也不轮播空闲动作（否则 30 秒到点会跟调度层抢帧）。
    this.hold = false;
    canvas.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        activate();
      }
    });
    if (shell) shell.onCursor((point) => this.cursor(point));
  }

  async load() {
    const response = await fetch('/static/nailong/manifest.json');
    if (!response.ok) throw new Error('奶龙动作表读取失败');
    this.manifest = await response.json();
    for (const animation of this.manifest.animations) {
      this.animations.set(animation.key, animation);
    }
    const names = new Set(this.manifest.animations.flatMap((animation) => animation.frames.map((frame) => frame.name)));
    await Promise.all([...names].map(async (name) => {
      const image = new Image();
      const loaded = new Promise((resolve, reject) => {
        image.onload = resolve;
        image.onerror = () => reject(new Error(`奶龙帧缺失：${name}`));
      });
      image.src = `/static/nailong/frames/${name}.png`;
      await loaded;
      this.images.set(name, image);
    }));
    // All frames share one hit mask so changing poses cannot turn a grabbed pixel transparent.
    const mask = document.createElement('canvas');
    mask.width = this.manifest.meta.cellW;
    mask.height = this.manifest.meta.cellH;
    const ctx = mask.getContext('2d', { willReadFrequently: true });
    for (const animation of this.animations.values()) {
      for (const frame of animation.frames) {
        ctx.drawImage(this.images.get(frame.name), animation.ox, frame.oy, frame.w, frame.h);
      }
    }
    this.mask = ctx.getImageData(0, 0, mask.width, mask.height).data;
    this.canvas.dataset.animations = String(this.animations.size);
    this.canvas.dataset.frames = String(names.size);
    this.switchTo('idle');
    requestAnimationFrame((now) => this.tick(now));
  }

  hitTest(point) {
    if (!this.mask) return false;
    const rect = this.canvas.getBoundingClientRect();
    const x = Math.floor((point.x - rect.x) * this.canvas.width / rect.width);
    const y = Math.floor((point.y - rect.y) * this.canvas.height / rect.height);
    if (x < 0 || y < 0 || x >= this.canvas.width || y >= this.canvas.height) return false;
    return this.mask[(y * this.canvas.width + x) * 4 + 3] > 16;
  }

  cursor(point) {
    if (!this.shell) return;
    // 常驻的小气泡只做 hover 目标，始终点击穿透；详情框展开后 pointer-events 变 auto，才接管鼠标。
    const element = document.elementFromPoint(point.x, point.y);
    const interactive = element?.closest('.panel,.bubble-detail') || this.hitTest(point);
    const ignore = !interactive;
    if (ignore !== this.ignoreMouse) {
      this.ignoreMouse = ignore;
      this.shell.setMousePassthrough(ignore);
    }
  }

  switchTo(key, force = false) {
    if (['run', 'walk', 'stroll'].includes(key)) return false;
    const animation = this.animations.get(key);
    if (!animation) return false;
    this.current = animation;
    this.startedAt = performance.now();
    this.idleAt = this.startedAt + 30000;
    this.frameIndex = -1;
    this.canvas.dataset.action = key;
    return true;
  }

  // 会话 5：调度层接管/交还。「接管期间不自动回 idle、不轮播 idle」是硬要求，
  // 否则被提起来 / 思考中的姿态会在 30 秒到点被 tick() 抢回 idle。
  setHold(hold) {
    const next = Boolean(hold);
    if (next === this.hold) return;
    this.hold = next;
    if (!next) this.idleAt = performance.now() + 30000;
  }

  tick(now) {
    requestAnimationFrame((time) => this.tick(time));
    if (document.hidden || !this.current) return;
    const animation = this.current;
    const count = animation.frames.length;
    const elapsed = Math.floor(Math.max(0, now - this.startedAt) * animation.fps / 1000);
    if (!this.hold) {
      if (!animation.loop && !animation.hold && elapsed >= count * (animation.repeat || 1)) {
        this.switchTo('idle');
        return;
      }
      if (now >= this.idleAt) {
        const actions = ['idle2', 'sit', 'idle3', 'sleep', 'idle4', 'idle'];
        this.switchTo(actions[this.idleIndex++ % actions.length]);
        return;
      }
    }
    // 被调度层持有的动作一律按循环放帧：work/laugh/shy 这些 repeat 有限的动作
    // 不能自己播完就定住，否则「思考中」几分钟后画面就僵在那里。
    const index = (this.hold || animation.loop)
      ? elapsed % count
      : Math.min(elapsed, count * (animation.repeat || 1) - 1) % count;
    if (index === this.frameIndex) return;
    this.frameIndex = index;
    const frame = animation.frames[index];
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.ctx.drawImage(this.images.get(frame.name), animation.ox, frame.oy, frame.w, frame.h);
  }
}

window.PetController = PetController;
