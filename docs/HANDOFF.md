# 队友交接说明 · 奶龙桌宠

> 本文件是**给接手开发者的交接件**，随仓库发布。clone 后按第 1 节做即可跑起来。
> 项目定位与能力说明见根目录 `README.md`，发布说明见 `docs/RELEASE.md`。
> 本仓库是独立的桌宠项目：公开材料以桌宠为主体，内嵌的智能体能力作为可选功能如实说明。

## 目录整理后的入口

- 桌宠：根目录 `启动.bat`，或 `apps/deskpet/desktop/start.bat`。
- 前端页面与 Electron、奶龙素材：`apps/deskpet/desktop/`（页面在 `renderer/`）。
- 后端：`apps/deskpet/backend/`（FastAPI、SQLite、模型网关、能力注册表、完整 Agent 运行时）。
- 桌宠内嵌 Agent 的工具与运行时实现：`apps/agent/`（按需加载，不是独立产品入口）。
- 共享基础能力：`infrastructure/`、`config/`；模型关闭时保持懒加载。
- 测试：`tests/backend/`、`tests/desktop/`、`tests/agent/`、`tests/infrastructure/`。
- 下节测试数字为 2026-11-07 实测基线，改动后以实际命令输出为准。

## 0. 当前状态（2026-11-07）

- 阶段 1–5 已完成；此后增补了自由闲聊与可选的完整 Agent 模式，并下架了早期的小游戏模块。
- 独立复测：`verify.py --level full` **21/22**（唯一未绿项是 `pytest_green`：273 passed + 6 failed，6 个失败全部来自 `tests/agent/test_registry.py` 中 import 路径未随目录重构同步，属测试代码自身，不影响运行）；接口契约冒烟 **34 PASS / 0 FAIL**；`npm test` **32/32**；桌面壳自检 `DESKTOP_SMOKE pass:true`（退出码 0）。
- 现在能跑的是「透明置顶桌面小窗 + 规则版主流程 + 可选模型 / Agent 能力」，**还不是安装包**。
- 阶段 6（博客 / PPT / 需求文档 / 答辩）、阶段 7（打包 exe + GitHub Release）**未开工**。
- 仓库不含 `.env`、`.venv/`、`apps/deskpet/desktop/node_modules/`、`data/`，也不含任何预置或演示数据。
- 已知不一致：`apps/deskpet/backend/tools_allowlist.py` 的文件头注释仍写「不构造 ReAct Agent、不注册任何工具」「不做通用问答 / 联网搜索」，那是**主线**的历史口径；Agent 模式启用后的实际行为以下方 §3 双轨说明为准。注释待后续顺手同步，不要据它改删功能。

## 1. clone 后重建环境（Windows / PowerShell）

前置：本机已装 Python 与 Node.js。**实测可用版本：Python 3.13.14、Node v22.22.2**；更低版本未验证，建议先对齐这两版。

```powershell
Set-Location '<项目目录>'
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
cd apps/deskpet/desktop
npm install
```

- `requirements-rag.txt` 是可选扩展（只多 `langchain-chroma` / `chromadb`），**不装不影响主流程与全套自检**，`infrastructure/retrieval/rag.py` 有 `ImportError` 保护会自动跳过。
- **不要创建 `.env`**：桌宠运行不需要它。Key 由使用者在应用内填写，见 `README.md` 第三节。
- 启动（在 `apps/deskpet/desktop/` 下）：

```powershell
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
npm start
```

  若本机存在 `ELECTRON_RUN_AS_NODE=1`，Electron 会走 Node 模式并报 `app undefined`，必须先清掉；Git Bash 用 `env -u ELECTRON_RUN_AS_NODE npm start`。不要带 `NAIWA_SMOKE_TEST=1`（那是自动自检，跑完即退出）。

## 2. 每次改动后怎么自检

```powershell
# 项目根：22 项机械检查 + 全套 pytest + 真实 HTTP
.\.venv\Scripts\python.exe docs\验收集\verify.py --level full --root .

# apps/deskpet/desktop/ 下：桌面壳自检（自动退出，只用于出证据）
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
$env:NAIWA_SMOKE_TEST = '1'
.\node_modules\electron\dist\electron.exe .
Remove-Item Env:NAIWA_SMOKE_TEST -ErrorAction SilentlyContinue
```

判据：`full` 21/22 且唯一失败为上述 import 路径项、冒烟 34/0、真实 HTTP 全绿、`model_calls=0`、`DESKTOP_SMOKE pass:true`（初始态干净、13 组动作 / 74 帧、动态端口仅绑 `127.0.0.1`、渲染进程 / 命令行 / 日志三处明文 Key 零泄漏、关窗进托盘与系统通知正常）。把 6 处 import 同步后应回到 `full 22/22`。

**注意**：现有验收集只覆盖阶段 1–5，**不检查 exe 在无 Python 的干净机器上能否双击启动**；阶段 7 的验收要另写，两者不能互相顶替。

## 3. 不许改的红线（代码级）

**目录与路径**
- `apps/deskpet/desktop/main.js`：`ROOT = path.resolve(__dirname, '../../..')`、`PYTHON = ROOT/.venv/Scripts/python.exe`、`spawn(PYTHON, ['-m', 'uvicorn', 'apps.deskpet.backend.app:app', ...], { cwd: ROOT })`，并注入 `PYTHONPATH=ROOT` / `DESKPET_DATA_DIR`。调整目录时必须同步修改路径解析和测试。
- `apps/deskpet/backend/app.py` 挂 `../desktop/renderer/` 与 `../desktop/assets/nailong/`；`apps/deskpet/backend/llm.py` 找 `config/models.json`。
- `app.getPath('userData')` 是 Electron API，Python 侧统一走 `apps/deskpet/backend/paths.py`。

**模型层：两条轨道，不要混为一谈**

*主线（贴通知出清单、一句话变待办、任务内问答、自由闲聊）——收敛的结构化调用*
- 只允许经 `apps/deskpet/backend/llm.py`（LlmGateway）：`ModelManager` + `with_structured_output(Schema).ainvoke()`，固定 JSON schema、重试 ≤2、**工具表 0**；不调 `init_runtime()` / `get_agent()` / `switch_model()`。
- `apps/deskpet/backend/tools_allowlist.py` 的 `FORBIDDEN_TOOLS`（含 `search_web`）是主线配置层禁用清单，**不许放宽**；`assert_no_forbidden()` 目前只在启动时检查 `ALLOWED_DESKPET_TOOLS` 自身，不要误以为它已经覆盖了第四态。
- `switch()` 传 `config/models.json` 的**目录 id**（`deepseek-chat` / `deepseek-reasoner` / `ollama-local`），不是 `model` 字段的值；`max_input_tokens=131072` 是压缩预算，不要调成 1M。
- 四个易错口径：`enable_llm_cache(db_path)` 必须传参且只在网关启用时懒加载；`estimate_cost(pricing, input, output)` 三参；`with_structured_output` 返回 Pydantic 对象，拿不到 `usage_metadata` 要标「用量未知」；`expires_at` 是必填响应字段，只能真清理。
- 五个扩展口子保持现状：单一 `LlmGateway` 入口、prompt / schema 独立可替换、`capabilities.py` 四声明字段、配额重试集中在网关、检索隔离。

*Agent 模式（第四态，默认关）——完整工具链，独立运行时*
- 实现只在 `apps/deskpet/backend/agent_runtime.py`，用 `create_agent()` 自行组装，**不用** `apps/agent/runtime/core.py` 的 `build_agent()`（后者会带上通用任务中间件与另一套限额）。
- 它是 `off / auto / on` 三态之外的**叠加开关**（由 `POST /api/agent/mode` 独立持久化，不替换三态）：只有「有 Key + 用户显式打开」才触发；三态语义一字未动，关闭后相关模块立即卸载。
- 硬约束不许放宽：`APPROVAL_TOOLS`（`run_shell` / `write_file` / `edit_file` / `open_url`）必须主进程模态审批，审批绑定精确参数、60 秒未响应默认拒绝；`SEARCH_ALLOWED_HOSTS` 域名白名单与检索结果注入清洗（`external_content trust="untrusted"` 定界符）不得删；单次对话 ≤8 次模型调用 / ≤10 次工具调用 / 90 秒墙钟；与网关共用同一份成本账本。
- **关态铁闸**：`agent_runtime.py` 顶层绝不静态 import `langchain*` / `apps.agent.tools*` / `infrastructure.cache`，一律 `importlib.import_module()` 懒加载；`verify.py` 的 `BANNED_MODULES` 与 `no_tools_import` 两项必须保持绿。三态中 `关` 态零加载、`自动` 无 Key 静默走规则版、`开` 失败如实报错，不许假装成功。

**安全与素材**
- Key：使用者自填 → `safeStorage` 加密存 `userData` → 启动时解密后**只注入 Python 子进程环境变量**；不进仓库、包、日志、渲染进程明文。界面只显示脱敏尾四位。
- `apps/deskpet/desktop/assets/nailong/`：74 张 PNG + `manifest.json` + `ASSET-LICENSE.md` 原样保留，不改帧 / 不补帧 / 不调色；三处 CC BY 4.0 署名（根 `README.md`、`docs/RELEASE.md`、应用内「关于」）不可删。`apps/deskpet/desktop/vendor/petex/` 的 Apache-2.0 `LICENSE` / `SOURCE.md` 同样随包保留。

**产品**
- 每位使用者独立培养、从干净「认识你的第 1 天」开始；**禁止任何预置数据、演示故事或演示模式**。
- 模拟 7/30/100 天只覆盖展示日期、不写脏真实首见日期。
- 答辩演示只走本机真实状态三步：规则版主流程 → 记忆补报「还差什么」→ 托盘 + 系统通知；无需 Key / 联网，不演模拟天数、不放录屏。自由闲聊与 Agent 模式默认关闭，只在 PPT 与问答中说明。

## 4. 阶段 6 待办（文档与答辩）

- 博客、PPT 三段式、国标需求文档；对照评分表逐条勾：固定表格字段、队员风采、首次合照、100 字愿景、迭代记录表 / 思维导图 / 类图、功能描述与验收标准且前后端分开。
- 三步演示脚本与彩排 ≤4 分钟，全程本机真实养出的历史。
- 被问到「智能体体现在哪」时，分两层讲清楚：主线是单一入口 + 循环 ≤2 + 工具表 0 + 固定 JSON 的收敛决策；第四态是完整工具链 + 审批 + 域名白名单 + 默认关闭。清单是智能体的**任务出口**，不是智能体本体；不要当场打开别的程序。

## 5. 阶段 7 待办（打包 exe，P0 处方）

**现状问题**：`apps/deskpet/desktop/package.json` 的 `build.files` 只含桌面端文件（主进程、preload、原生点击脚本、renderer、素材、petex），尚未包含 `apps/deskpet/backend/`、`apps/agent/`、`infrastructure/`、`config/` 与 Python 运行时；`ROOT` 和 `.venv` 查找仍面向源码目录。此外 `storage.py` 的 `DB_PATH` 相对启动目录，装到只读安装目录会写不进。

要做的：

1. 把 `apps/deskpet/backend/`、`apps/agent/`、`infrastructure/`、`config/`、Python 运行时纳入 `build.files` 或 `extraResources`；**同时改路径解析**，让打包后的 `ROOT` / `PYTHON` / `PYTHONPATH` 指向解包后的真实位置，不能只改配置表；业务库改落可写的用户数据目录。
2. 显式排除 `.env`（真实 Key），解包后全局搜 Key 字样；验证启动链不 `load_dotenv`。
3. 同步 `tests/agent/test_registry.py` 的 6 处 import 路径，让 `full` 回到 22/22。
4. 另写一套阶段 7 验收：**无 Python 的干净机器**双击安装 → 断网走完规则版主流程 → 包里零 Key → 托盘 / 系统通知正常 → 三步演示 ≤4 分钟。
5. 打包前跑一次 `verify.py --level full`；打包后在干净机器实测。两者都要有证据，不能互相顶替。
6. 发布后补 Release 说明与本机真实截图 / GIF。

## 6. 交付与协作

- 主交付：Windows exe 放 GitHub Release（+ 静态介绍页双保险）；现场演示本机真实运行。
- 交接一律用 `git clone`，不要用 Download ZIP / 网页拖文件（会丢分批 commit，也会绕过 `.gitignore`）。
- 仓库内交接依据 = 本文件 + `README.md` + `docs/RELEASE.md` + `docs/验收集/verify.py`；**更细的过程记录不随仓库发布**，提交时用精确路径 `git add`，不要 `git add .` 把本地验收清单与原始输出目录一起推上去。
