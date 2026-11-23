const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const appSource = fs.readFileSync(
  path.resolve(__dirname, '../../apps/deskpet/desktop/renderer/app.js'), 'utf8'
);

function documentApi(fetch) {
  const window = {};
  vm.runInNewContext(appSource.replace('const app = new DeskpetApp();\n  app.init();',
    'window.Api = Api;'), { window, fetch, URLSearchParams, TextDecoderStream });
  return Object.assign(Object.create(window.Api.prototype), { uid: 'local-user' });
}

test('文档上传流分块时保留中文文件名、进度和最终结果', async () => {
  const file = new File(['请上传实验报告'], '报告 & 时间.txt');
  const progress = { type: 'progress', stage: 'extract', processed: 1, total: 2, location: '第 1 页' };
  const result = { type: 'result', mode: 'rules', data: { items: [{ text: '提交报告', source: '第 2 页' }] } };
  const wire = Buffer.from(JSON.stringify(progress) + '\n' + JSON.stringify(result));
  const split = wire.indexOf(Buffer.from('页')) + 1;
  const api = documentApi(async (url, options) => {
    assert.equal(url, '/api/notice/document?filename=%E6%8A%A5%E5%91%8A+%26+%E6%97%B6%E9%97%B4.txt&mode=agent');
    assert.equal(options.headers['X-User-Id'], 'local-user');
    assert.equal(options.headers['Content-Type'], 'application/octet-stream');
    assert.equal(options.body, file);
    return new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(wire.subarray(0, split));
        controller.enqueue(wire.subarray(split));
        controller.close();
      }
    }));
  });
  const updates = [];
  const parsed = await api.parseNoticeDocument(file, 'agent', (event) => updates.push(event));
  assert.deepEqual(JSON.parse(JSON.stringify(updates)), [progress]);
  assert.deepEqual(JSON.parse(JSON.stringify(parsed)), result);
});

test('服务在文档处理中报错时展示具体原因', async () => {
  const api = documentApi(async () => new Response('{"type":"error","message":"无法读取加密 PDF"}\n'));
  await assert.rejects(
    api.parseNoticeDocument(new File(['PDF'], 'notice.pdf'), 'standard', () => {}),
    /无法读取加密 PDF/
  );
});
