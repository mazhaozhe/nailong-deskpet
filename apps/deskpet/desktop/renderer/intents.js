// 意图识别（会话 5）：桌宠「听懂了要干什么」的纯规则层，零模型、零网络。
// 两件事：
//   1. 用户说「帮我打开 https://…」→ 交给桌面壳真打开（主进程还要再过一遍白名单）；
//   2. 用户说「玩一局 / 帮我写文档」→ 走拟人任务演出（纯 DOM 小画面，不驱动真浏览器）。
// 红线：模型输出里夹带的 URL 一律不自动打开（search_web 会带回不可信网页内容）。
// 本文件同时被主进程 require（app:open-external 的白名单），所以保持 CommonJS 兼容。
'use strict';

// 白名单：只有这些站点允许被桌宠「亲自打开」。
// 收紧的理由：openExternal 一旦接上意图识别，任何一条注入都能借它拉起外部程序，
// 白名单把爆炸半径限定在常用只读站点上。
const OPEN_ALLOWED_HOSTS = Object.freeze([
  'github.com',
  'gitee.com',
  'docs.qq.com',
  'deepseek.com',
  'bilibili.com',
  'juejin.cn',
  'developer.mozilla.org',
  'python.org',
  'pypi.org',
  'electronjs.org',
  'fastapi.tiangolo.com',
  'creativecommons.org'   // 关于页的许可链接，属本项目自有署名链接
]);

const OPEN_VERBS = ['打开', '访问', '帮我开', '给我开', '去看看', '看看', 'open'];
const URL_RE = /https?:\/\/[^\s"'<>()（）【】，。；、]+/i;

// 内网 / 本机 / 保留地址：一律拒绝（https + 白名单两重之外再加一道）
const PRIVATE_HOST_RE = /^(localhost|127\.|0\.0\.0\.0$|10\.|192\.168\.|169\.254\.|::1$|\[::1\]$)/i;
const PRIVATE_172_RE = /^172\.(1[6-9]|2\d|3[0-1])\./;

function hostAllowed(hostname) {
  const host = String(hostname || '').toLowerCase();
  if (!host) return false;
  return OPEN_ALLOWED_HOSTS.some((allowed) => host === allowed || host.endsWith('.' + allowed));
}

/**
 * 这个 URL 能不能交给系统浏览器打开。
 * 只放行 https + 白名单域名 + 非内网 + 不带凭据。
 */
function isAllowedExternalUrl(rawUrl) {
  let parsed = null;
  try {
    parsed = new URL(String(rawUrl || ''));
  } catch {
    return false;
  }
  if (parsed.protocol !== 'https:') return false;          // http / file / javascript / data 全拒
  if (parsed.username || parsed.password) return false;    // 不允许 https://user:pass@host
  if (parsed.port && parsed.port !== '443') return false;  // 非标准端口一律不放行
  const host = parsed.hostname.toLowerCase();
  if (PRIVATE_HOST_RE.test(host) || PRIVATE_172_RE.test(host)) return false;
  if (host.endsWith('.local') || host.endsWith('.internal')) return false;
  return hostAllowed(host);
}

function extractUrl(text) {
  const match = String(text || '').match(URL_RE);
  return match ? match[0].replace(/[.,;:!?]+$/, '') : '';
}

/**
 * 「打开网址」意图。只认用户自己说的话：
 * @param {string} text 用户输入
 * @param {{fromModel?: boolean}} [options] fromModel=true 时永远返回 null
 * @returns {{url: string, allowed: boolean}|null}
 */
function detectOpenIntent(text, { fromModel = false } = {}) {
  if (fromModel) return null;   // 模型/检索摘要里夹带的 URL 不得自动打开
  const body = String(text || '');
  const url = extractUrl(body);
  if (!url) return null;
  const lowered = body.toLowerCase();
  if (!OPEN_VERBS.some((verb) => lowered.includes(verb))) return null;
  return { url, allowed: isAllowedExternalUrl(url) };
}

const TASK_RULES = [
  { kind: 'play', label: '正在玩一局', re: /玩(一局|一下|一玩|个游戏)|小游戏|陪我玩|打一局/ },
  { kind: 'doc', label: '正在写文档', re: /写(一份|一个|个|篇)?(文档|报告|作业|总结|计划)|导出|整理成文档|做成文档/ }
];

/**
 * 「拟人任务」意图：玩游戏 / 写文档。
 * @returns {{kind: 'play'|'doc', label: string}|null}
 */
function detectTaskIntent(text) {
  const body = String(text || '');
  for (const rule of TASK_RULES) {
    if (rule.re.test(body)) return { kind: rule.kind, label: rule.label };
  }
  return null;
}

const api = {
  OPEN_ALLOWED_HOSTS,
  isAllowedExternalUrl,
  detectOpenIntent,
  detectTaskIntent,
  extractUrl
};

if (typeof module !== 'undefined' && module.exports) module.exports = api;
if (typeof window !== 'undefined') window.DeskpetIntents = api;
