// Key 输入弹窗的 preload：只做「把用户输入交回主进程」这一件事。
// 每次弹窗带一个随机 token，主进程按 token 分发，避免多个窗口互相串台。
const { contextBridge, ipcRenderer } = require('electron');

const token = new URLSearchParams(location.search).get('token') || '';

function send(action, value) {
  ipcRenderer.send('key-dialog:result', { token, action, value: value || '' });
}

contextBridge.exposeInMainWorld('keyDialog', {
  submit: (value) => send('submit', value),
  cancel: () => send('cancel')
});
