const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('deskpetShell', {
  getRuntime: () => ipcRenderer.invoke('runtime:get'),
  getSettings: () => ipcRenderer.invoke('desktop:settings:get'),
  identify: (uid) => ipcRenderer.send('user:identity', uid),
  setMousePassthrough: (ignore) => ipcRenderer.send('mouse:passthrough', ignore),
  contextMenu: () => ipcRenderer.send('pet:context-menu'),
  setAlwaysOnTop: (value) => ipcRenderer.send('desktop:top', value),
  setMuted: (value) => ipcRenderer.send('desktop:mute', value),
  setTheme: (value) => ipcRenderer.send('desktop:theme', value),
  changeKey: () => ipcRenderer.invoke('key:change'),
  deleteKey: () => ipcRenderer.invoke('key:delete'),
  checkKey: () => ipcRenderer.invoke('key:check'),
  testNotification: () => ipcRenderer.invoke('notification:test'),
  openExternal: (url) => ipcRenderer.send('app:open-external', url),
  // Agent 审批：渲染层发起弹窗请求，主进程弹真实模态窗并回传决定数组
  requestApproval: (payload) => ipcRenderer.invoke('approval:request', payload),
  onCommand: (callback) => {
    const listener = (_event, command) => callback(command);
    ipcRenderer.on('pet:command', listener);
    return () => ipcRenderer.removeListener('pet:command', listener);
  },
  onSettings: (callback) => {
    const listener = (_event, settings) => callback(settings);
    ipcRenderer.on('desktop:settings', listener);
    return () => ipcRenderer.removeListener('desktop:settings', listener);
  },
  onCursor: (callback) => {
    const listener = (_event, point) => callback(point);
    ipcRenderer.on('pet:cursor', listener);
    return () => ipcRenderer.removeListener('pet:cursor', listener);
  },
  platform: process.platform
});
