// assemble-runtime.mjs —— 组装「包内 Python 运行时」
//
// 为什么不能直接拷 .venv（实测结论，别再踩）：
//   .venv/pyvenv.cfg 的 home 指向本机绝对路径，Lib 下只有 site-packages，
//   标准库在 base 的 Lib/、扩展模块在 base 的 DLLs/、python313.dll 与
//   vcruntime 都在 base 根目录。只拷 .venv，换台机器 python.exe 直接起不来。
//
// 所以这里拼一份「完整 CPython 安装布局」：
//   runtime/
//     python.exe  python3.dll  python313.dll  vcruntime140*.dll   ← base 解释器
//     DLLs/                                                      ← 扩展模块 .pyd + 依赖 dll
//     Lib/                                                       ← 标准库（去 .pyc / 去测试件）
//     Lib/site-packages/                                         ← .venv 里的运行依赖
// 没有 pyvenv.cfg：python.exe 会把自己的目录当 sys.prefix，site 自动挂
// Lib/site-packages，.pth 也照常处理（不像 embeddable 的 ._pth 会关掉 site）。
//
// 产物是构建中间物，不进仓库（.gitignore 已排除 build-runtime/）。
// 用法：node scripts/assemble-runtime.mjs   （npm run dist 的 predist 会先跑它）

import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DESKTOP_DIR = path.resolve(HERE, '..');
const REPO_ROOT = path.resolve(DESKTOP_DIR, '..', '..', '..');
const VENV = path.join(REPO_ROOT, '.venv');
const OUT = path.join(DESKTOP_DIR, 'build-runtime', 'runtime');

const log = (...args) => console.log('[runtime]', ...args);
const mb = (bytes) => `${(bytes / 1024 / 1024).toFixed(1)} MB`;

function fail(message) {
  console.error(`[runtime] 失败：${message}`);
  process.exit(1);
}

function readPyvenvHome(venvDir) {
  const cfg = path.join(venvDir, 'pyvenv.cfg');
  if (!fs.existsSync(cfg)) fail(`找不到 ${cfg}，请先在仓库根建好 .venv`);
  const text = fs.readFileSync(cfg, 'utf8');
  const line = text.split(/\r?\n/).find((item) => /^\s*home\s*=/.test(item));
  if (!line) fail('pyvenv.cfg 里没有 home，无法定位 base Python');
  return line.split('=').slice(1).join('=').trim();
}

function dirSize(dir) {
  let total = 0;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) total += dirSize(full);
    else if (entry.isFile()) total += fs.statSync(full).size;
  }
  return total;
}

// 标准库里跟本产品无关的整目录：测试件、GUI、打包工具链
const STDLIB_SKIP_DIRS = new Set([
  'site-packages', 'test', 'tests', 'idlelib', 'tkinter', 'turtledemo',
  'ensurepip', 'lib2to3', '__pycache__', 'pydoc_data'
]);
// 开发期才会用到的 site-packages 顶层条目
const SITE_SKIP = [
  /^pip(-|$)/i,
  /^pytest(-|$)/i,
  /^_pytest(-|$)/i,
  /^py(-|$)/i,
  /^py\.py$/i,
  /^setuptools(-|$)/i,
  /^pkg_resources(-|$)/i,
  /^_distutils_hack(-|$)/i,
  /^wheel(-|$)/i,
  // 只在开发机上装了、产品运行时零引用，合计约 186 MB，不进包：
  //   pymupdf / pymupdf4llm / pymupdf_layout —— PDF 解析实际走 pypdf（apps/agent/runtime/uploads.py）；
  //   onnxruntime —— pymupdf 的 layout 模型运行时，随 pymupdf 一起进来的；
  //   matplotlib —— 只服务离线出图脚本（需求文档 gen_figs.py），产品内无 import。
  /^pymupdf/i,
  /^onnxruntime(-|$)/i,
  /^matplotlib(-|$)/i
];

const skipStdlibDir = (name) => STDLIB_SKIP_DIRS.has(name);
const skipStdlibFile = (name) => /\.(pyc|pyo|pdb)$/i.test(name) || /^bad_coding/i.test(name);
const skipSiteEntry = (name) => SITE_SKIP.some((re) => re.test(name));

function copyTree(from, to, { dirFilter = () => true, fileFilter = () => true } = {}) {
  fs.mkdirSync(to, { recursive: true });
  for (const entry of fs.readdirSync(from, { withFileTypes: true })) {
    const src = path.join(from, entry.name);
    const dest = path.join(to, entry.name);
    if (entry.isDirectory()) {
      if (!dirFilter(entry.name)) continue;
      copyTree(src, dest, { dirFilter, fileFilter });
    } else if (entry.isFile()) {
      if (!fileFilter(entry.name)) continue;
      fs.copyFileSync(src, dest);
    }
  }
}

function main() {
  const baseDir = readPyvenvHome(VENV);
  if (!fs.existsSync(path.join(baseDir, 'python.exe'))) {
    fail(`pyvenv.cfg 的 home 指向的目录里没有 python.exe：${baseDir}`);
  }
  const venvSite = path.join(VENV, 'Lib', 'site-packages');
  if (!fs.existsSync(venvSite)) fail(`找不到 ${venvSite}`);

  log(`base    = ${baseDir}`);
  log(`venv    = ${VENV}`);
  log(`输出     = ${OUT}`);

  fs.rmSync(path.join(DESKTOP_DIR, 'build-runtime'), { recursive: true, force: true });
  fs.mkdirSync(OUT, { recursive: true });

  // 1) 解释器本体 + 运行时 DLL（venv 里没有这些，必须从 base 取）
  const rootFiles = fs.readdirSync(baseDir, { withFileTypes: true })
    .filter((entry) => entry.isFile())
    .map((entry) => entry.name)
    .filter((name) => /^python(w)?\.exe$/i.test(name)
      || /^python3(d)?\.dll$/i.test(name)
      || /^python3\d+\.dll$/i.test(name)
      || /^vcruntime140(_1)?\.dll$/i.test(name));
  if (!rootFiles.some((name) => /^python\.exe$/i.test(name))) fail('base 目录缺少 python.exe');
  for (const name of rootFiles) {
    fs.copyFileSync(path.join(baseDir, name), path.join(OUT, name));
  }
  log(`解释器文件 ${rootFiles.length} 个：${rootFiles.join(' ')}`);

  // 2) DLLs/：扩展模块与它们的 dll 依赖，去掉测试用 pyd
  const dllsDir = path.join(baseDir, 'DLLs');
  if (!fs.existsSync(dllsDir)) fail('base 目录缺少 DLLs/');
  copyTree(dllsDir, path.join(OUT, 'DLLs'), {
    fileFilter: (name) => !/\.pdb$/i.test(name) && !/^_test/i.test(name) && !/^_ctypes_test/i.test(name)
  });

  // 3) Lib/：标准库（不含 site-packages）
  copyTree(path.join(baseDir, 'Lib'), path.join(OUT, 'Lib'), {
    dirFilter: (name) => !skipStdlibDir(name),
    fileFilter: (name) => !skipStdlibFile(name)
  });

  // 4) Lib/site-packages/：.venv 的运行依赖，去掉 pip / pytest 与字节码缓存
  copyTree(venvSite, path.join(OUT, 'Lib', 'site-packages'), {
    dirFilter: (name) => name !== '__pycache__' && !skipSiteEntry(name),
    fileFilter: (name) => !/\.(pyc|pyo)$/i.test(name)
  });

  // 5) 自检：用刚拼好的解释器在干净 env 里 import 一遍
  const probe = 'import sys, fastapi, uvicorn, docx, langchain, langgraph, sqlalchemy;'
    + 'print("version=" + sys.version.split()[0]);'
    + 'print("prefix=" + sys.prefix);'
    + 'print("sqlite=" + __import__("sqlite3").sqlite_version)';
  const env = { ...process.env, PYTHONPATH: '', PYTHONHOME: '', PYTHONDONTWRITEBYTECODE: '1' };
  delete env.PYTHONPATH;
  delete env.PYTHONHOME;
  let probeOut = '';
  try {
    probeOut = execFileSync(path.join(OUT, 'python.exe'), ['-c', probe], {
      cwd: path.join(OUT, 'Lib'), env, encoding: 'utf8', timeout: 120000
    }).trim();
  } catch (error) {
    fail(`拼好的运行时 import 自检失败：${(error.stderr || error.message).toString().slice(0, 800)}`);
  }
  const size = dirSize(OUT);
  log(`自检通过：\n${probeOut.split(/\r?\n/).map((line) => `         ${line}`).join('\n')}`);
  log(`运行时体积 ${mb(size)}`);
  fs.writeFileSync(path.join(DESKTOP_DIR, 'build-runtime', 'runtime-manifest.json'), JSON.stringify({
    built_at: new Date().toISOString(),
    base_python: baseDir,
    bytes: size,
    probe: probeOut.split(/\r?\n/)
  }, null, 2), 'utf8');
}

main();
