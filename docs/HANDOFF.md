# 队友交接说明 · 奶龙桌宠

> 本文件是**给接手开发者的交接件**，随仓库发布。clone 后按第 1 节做即可跑起来。
> 项目定位与能力说明见根目录 `README.md`，发布说明见 `docs/RELEASE.md`。
> 本仓库是独立的桌宠项目，公开材料只讲桌宠。

## 0. 当前状态（2026-11-06）

- 阶段 1–5 已完成并独立复测：`verify.py --level full` **22/22、ok:true**、pytest **249 passed**、桌面壳自检 `DESKTOP_SMOKE pass:true`。
- 阶段 6（博客 / PPT / 需求文档 / 答辩）、阶段 7（打包 exe + GitHub Release）**未开工**。
- 现在能跑的是「透明置顶桌面小窗 + 规则版主流程」，**还不是安装包**。
- 仓库不含 `.env`、`.venv/`、`client/node_modules/`、`data/`，也不含任何预置或演示数据。

## 1. clone 后重建环境（Windows / PowerShell）

前置：本机已装 Python 与 Node.js。**实测可用版本：Python 3.13.14、Node v22.22.2**；更低版本未验证，建议先对齐这两版。

```powershell
Set-Location '<项目目录>'
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
cd client
npm install
```

- `requirements-rag.txt` 是可选扩展（只多 `langchain-chroma` / `chromadb`），**不装不影响主流程与全套自检**，`memory/rag.py` 有 `ImportError` 保护会自动跳过。
- **不要创建 `.env`**：桌宠运行不需要它。Key 由使用者在应用内填写，见 `README.md` 第三节。
- 启动（在 `client/` 下）：

```powershell
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
npm start
```

  若本机存在 `ELECTRON_RUN_AS_NODE=1`，Electron 会走 Node 模式并报 `app undefined`，必须先清掉；Git Bash 用 `env -u ELECTRON_RUN_AS_NODE npm start`。不要带 `NAIWA_SMOKE_TEST=1`（那是自动自检，跑完即退出）。

## 2. 每次改动后怎么自检

```powershell
# 项目根：22 项机械检查 + 全套 pytest + 真实 HTTP
.\.venv\Scripts\python.exe docs\验收集\verify.py --level full --root .

# client/ 下：桌面壳自检（自动退出，只用于出证据）
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
$env:NAIWA_SMOKE_TEST = '1'
.\node_modules\electron\dist\electron.exe .
Remove-Item Env:NAIWA_SMOKE_TEST -ErrorAction SilentlyContinue
```

判据：`full` 22/22 且 `ok:true`、`249 passed`、`DESKTOP_SMOKE pass:true`（初始态干净、13 组动作 / 74 帧、`model_calls=0`、动态端口仅绑 `127.0.0.1`、渲染进程 / 命令行 / 日志三处明文 Key 零泄漏、关窗进托盘与系统通知正常）。

**注意**：现有验收集只覆盖阶段 1–5，**不检查 exe 在无 Python 的干净机器上能否双击启动**；阶段 7 的验收要另写，两者不能互相顶替。

## 3. 不许改的红线（代码级）

**目录与路径**
- `client/main.js`：`ROOT = path.resolve(__dirname, '..')`、`PYTHON = ROOT/.venv/Scripts/python.exe`、`spawn(PYTHON, ['-m', 'uvicorn', 'deskpet.app:app', ...], { cwd: ROOT })`，并注入 `PYTHONPATH=ROOT` / `DESKPET_DATA_DIR`。目录层级不可改。
- `deskpet/app.py` 找 `../client/assets/nailong/`；`deskpet/llm.py` 找 `../config/models.json`。

**模型层**
- 调用**只允许经 `deskpet/llm.py`（LlmGateway）**：`ModelManager` + `with_structured_output(Schema).ainvoke()`，固定 JSON schema、重试 ≤2、工具表 0。
- 不要引入 `agent/core.py` 的 `build_agent()`，不要调 `init_runtime()` / `get_agent()` / `switch_model()`，不要在桌宠侧注册工具表。
- `switch()` 传 `config/models.json` 的**目录 id**（`deepseek-chat` / `deepseek-reasoner` / `ollama-local`），不是 `model` 字段的值；`max_input_tokens=131072` 是压缩预算，不要调成 1M。
- 三态开关（关 / 自动默认 / 开）全局生效：`关` 态懒加载不许 import `langchain_core` / `langchain_community` / `agent.models` / `memory.cache` / `tools`；`自动` 无 Key 静默走规则版、失败降级；`开` 失败如实报错，不许假装成功。
- 四个易错口径：`enable_llm_cache(db_path)` 必须传参且只在网关启用时懒加载；`estimate_cost(pricing, input, output)` 三参；`with_structured_output` 返回 Pydantic 对象，拿不到 `usage_metadata` 要标「用量未知」；`expires_at` 是必填响应字段，只能真清理。
- `app.getPath('userData')` 是 Electron API，Python 侧统一走 `deskpet/paths.py`。
- `deskpet/tools_allowlist.py` 的 `FORBIDDEN_TOOLS` 不许放宽（含 `search_web`）；prompt / schema 保持独立可替换、`deskpet/capabilities.py` 的能力声明字段、配额重试集中在网关、检索隔离，这五个口子保持现状。

**安全与素材**
- Key：使用者自填 → `safeStorage` 加密存 `userData` → 启动时解密后**只注入 Python 子进程环境变量**；不进仓库、包、日志、渲染进程明文。界面只显示脱敏尾四位。
- `client/assets/nailong/`：74 张 PNG + `manifest.json` + `ASSET-LICENSE.md` 原样保留，不改帧 / 不补帧 / 不调色；三处 CC BY 4.0 署名（根 `README.md`、`docs/RELEASE.md`、应用内「关于」）不可删。

**产品**
- 每位使用者独立培养、从干净「认识你的第 1 天」开始；**禁止任何预置数据、演示故事或演示模式**。
- 模拟 7/30/100 天只覆盖展示日期、不写脏真实首见日期；游戏彩蛋在模型不可用时照常能玩，禁止逐帧调模型。
- 答辩演示只走本机真实状态三步：规则版主流程 → 记忆补报「还差什么」→ 托盘 + 系统通知；无需 Key / 联网，不演模拟天数与游戏彩蛋、不放录屏。

## 4. 阶段 6 待办（文档与答辩）

- 博客、PPT 三段式、国标需求文档；对照评分表逐条勾：固定表格字段、队员风采、首次合照、100 字愿景、迭代记录表 / 思维导图 / 类图、功能描述与验收标准且前后端分开。
- 三步演示脚本与彩排 ≤4 分钟，全程本机真实养出的历史。
- 被问到模型层怎么做时，只口述「单一入口 + 循环 ≤2 + 工具表 0 + 固定 JSON + 三态开关」，不当场打开别的程序。

## 5. 阶段 7 待办（打包 exe，P0 处方）

**现状问题**：`client/package.json` 的 `build.files` 只有 `main.js` / `preload.js` / `assets/**/*` / `package.json`；但后端 FastAPI 与主体页面在 `deskpet/`，Python 运行时在 `.venv/`，`main.js` 还要 `cwd: ROOT` 与 `PYTHONPATH=ROOT`。**按现状打包的 exe 必然起不来。**

要做的：

1. 把 `deskpet/`（含 `static/`）、`config/models.json`、Python 运行时纳入 `build.files` 或 `extraResources`；**同时改路径解析**，让打包后的 `ROOT` / `PYTHON` / `PYTHONPATH` 指向解包后的真实位置，不能只改配置表。
2. 显式排除 `.env`（真实 Key），解包后全局搜 Key 字样；验证启动链不 `load_dotenv`。
3. 顺手清 P1：`deskpet/llm.py` 的 `/api/key/status` detail 仍写「safeStorage 加密份待阶段 5 主进程注入」，实际已接线。
4. 发布后补 P2：README 加下载 / Release 入口。
5. 另写一套阶段 7 验收：**无 Python 的干净机器**双击安装 → 断网走完规则版主流程 → 包里零 Key → 托盘 / 系统通知正常 → 三步演示 ≤4 分钟。
6. 打包前跑一次 `verify.py --level full`；打包后在干净机器实测。两者都要有证据，不能互相顶替。

## 6. 交付与协作

- 主交付：Windows exe 放 GitHub Release（+ 静态介绍页双保险）；现场演示本机真实运行。
- 交接一律用 `git clone`，不要用 Download ZIP / 网页拖文件（会丢分批 commit，也会绕过 `.gitignore`）。
- 仓库内交接依据 = 本文件 + `README.md` + `docs/RELEASE.md` + `docs/验收集/verify.py`；更细的过程记录不随仓库发布，需要时向项目负责人索取。
