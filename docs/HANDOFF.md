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

## 0. 当前状态

- 通知入口支持 PDF、DOCX 和 TXT。普通与 Agent 模式都先在本机提取文字和 OCR，清单保留来源，确认后写入待办。
- 新安装以首次安装日期为相识起点，源码启动以首次使用为起点。相识天数按北京时间计算，亲密度由聊天与待办结算。
- 已发布附件与当前源码的区别见 [发布说明](RELEASE.md)。

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

日常回归可单独运行 `pytest` 和 `npm test`。通知功能重点检查第 31 页混合 PDF、扫描件、Word 表格图片、两种拆解模式和确认写入。安装包需要另外验证。

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
- 答辩演示只走本机真实状态三步：规则版主流程 → 记忆补报「还差什么」→ 托盘 + 系统通知；无需 Key / 联网，不演模拟天数、不放录屏。自由闲聊与 Agent 模式默认关闭，只在 PPT 与问答中说明。

## 4. 阶段 6 待办（文档与答辩）

- 博客、PPT 三段式、国标需求文档；对照评分表逐条勾：固定表格字段、队员风采、首次合照、100 字愿景、迭代记录表 / 思维导图 / 类图、功能描述与验收标准且前后端分开。
- 三步演示脚本与彩排 ≤4 分钟，全程本机真实养出的历史。
- 被问到「智能体体现在哪」时，分两层讲清楚：主线是单一入口 + 循环 ≤2 + 工具表 0 + 固定 JSON 的收敛决策；第四态是完整工具链 + 审批 + 域名白名单 + 默认关闭。清单是智能体的**任务出口**，不是智能体本体；不要当场打开别的程序。

## 5. Windows 打包与验收

运行 `npm run dist --prefix apps/deskpet/desktop`。包内 Python 运行时包含 OCR 引擎和模型，业务数据落在 Electron 的用户数据目录。构建产物和运行时目录保留在本地。

本轮已在本机解包验证混合 PDF、扫描件、Word 图片和无 Key 回退。正式发布前还需在全新 Windows 环境测试安装、启动和离线拆解。发布附件与 SHA256 记录在 [发布说明](RELEASE.md)。

## 6. 交付与协作

- 主交付：Windows exe 放 GitHub Release（+ 静态介绍页双保险）；现场演示本机真实运行。
- 交接一律用 `git clone`，不要用 Download ZIP / 网页拖文件（会丢分批 commit，也会绕过 `.gitignore`）。
- 仓库内交接依据 = 本文件 + `README.md` + `docs/RELEASE.md` + `docs/验收集/verify.py`；**更细的过程记录不随仓库发布**，提交时用精确路径 `git add`，不要 `git add .` 把本地验收清单与原始输出目录一起推上去。
