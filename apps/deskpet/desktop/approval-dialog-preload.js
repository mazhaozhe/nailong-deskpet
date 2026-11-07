// 审批弹窗的 preload：只做「接收载荷 + 把用户决定交回主进程」两件事。
// 每次弹窗带一个随机 token，主进程按 token 分发，避免多个窗口互相串台。
// 决定数组必须与 action_requests 等长、按下标一一对应（后端还会再校验一次）。
const { contextBridge, ipcRenderer } = require('electron');

const token = new URLSearchParams(location.search).get('token') || '';

contextBridge.exposeInMainWorld('approvalDialog', {
  onPayload: (callback) => {
    ipcRenderer.on('approval:payload', (_event, data) => {
      // token 一致性：载荷里的 token 必须与本窗口 URL 里的一致
      if (data && data.token === token) callback(data);
    });
  },
  submit: (decisions) => {
    ipcRenderer.send('approval-dialog:result', { token, action: 'decide', decisions });
  },
  cancel: () => {
    ipcRenderer.send('approval-dialog:result', { token, action: 'cancel' });
  }
});
