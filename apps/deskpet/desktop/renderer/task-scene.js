// 拟人任务小画面（会话 5）：桌宠旁边的一块「我也在干活」的缩略画面。
// 纯 DOM + CSS，**不启动浏览器、不控制真实网页、不加 Playwright/CDP 依赖**。
// CSP 口径：只建节点 + textContent，绝不 innerHTML 拼模型输出，不引外链资源。
// 鼠标穿透：整块 pointer-events:none，纯展示，不进 pet.js 的穿透白名单。
'use strict';

class TaskScene {
  constructor(root) {
    this.root = root || null;
    this.titleNode = root ? root.querySelector('#taskSceneTitle') : null;
    this.bodyNode = root ? root.querySelector('#taskSceneBody') : null;
    this.visible = false;
    this.kind = '';
  }

  static available() {
    return Boolean(document.querySelector('#taskScene'));
  }

  _clear() {
    if (!this.bodyNode || typeof this.bodyNode.replaceChildren !== 'function') {
      if (this.bodyNode) this.bodyNode.textContent = '';
      return;
    }
    this.bodyNode.replaceChildren();
  }

  _line(width) {
    const line = document.createElement('i');
    // 宽度走预置 class，不写 inline style（CSP 的 style-src 只放行 'self' 样式表）
    line.className = `task-line w${width}`;
    line.textContent = '';
    return line;
  }

  _block(className, text) {
    const node = document.createElement('div');
    node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  /** @param {'doc'|'play'} kind */
  show(kind, label) {
    if (!this.root) return false;
    this.kind = kind === 'play' ? 'play' : 'doc';
    this.visible = true;
    if (this.titleNode) {
      this.titleNode.textContent = label || (this.kind === 'play' ? '正在玩一局' : '正在写文档');
    }
    this._clear();
    if (!this.bodyNode) return true;
    if (this.kind === 'doc') {
      this.bodyNode.append(this._block('task-doc-sheet'));
      const sheet = this.bodyNode.lastChild;
      sheet.append(this._block('task-doc-title', '待办交付清单'));
      [92, 78, 86, 64, 88, 70].forEach((width) => sheet.append(this._line(width)));
      this.bodyNode.append(this._block('task-caption', 'docx · 本地生成'));
    } else {
      const board = this._block('task-play-board');
      for (let i = 0; i < 36; i += 1) {
        const cell = document.createElement('i');
        cell.className = 'task-play-cell';
        if ([7, 14, 21, 22, 28].includes(i)) cell.classList.add('on');
        if (i === 20) cell.classList.add('food');
        board.append(cell);
      }
      this.bodyNode.append(board, this._block('task-caption', '规则局 · 旁边看着'));
    }
    this.root.hidden = false;
    return true;
  }

  hide() {
    this.visible = false;
    if (!this.root) return;
    this.root.hidden = true;
    this._clear();
  }
}

if (typeof module !== 'undefined' && module.exports) module.exports = { TaskScene };
if (typeof window !== 'undefined') window.TaskScene = TaskScene;
