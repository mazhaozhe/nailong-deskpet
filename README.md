# 奶龙桌宠 · Desktop Pet

> 一个常驻 Windows 桌面的电子伙伴：贴一张通知 → 拆成清单 → 落成待办 →
> 下次回来主动补报「还差什么」→ 导出 docx。

---

## 一、它能做什么

| 能力 | 说明 |
|---|---|
| **贴通知出清单** | 把一段作业通知拆成逐条可勾选的清单；陌生通知也能出结果，界面诚实标注「模型拆解 / 规则兜底」 |
| **跨会话记忆比对**（内核） | 记住上次交了什么，回来时主动补报「你以为交齐了，其实还差合照」——这是它和普通待办清单的区别 |
| **决策链可视化** | 把「原文 → 拆出哪些项 → 为什么判定还差」摊开给人看，不是黑箱一句话 |
| **导出 docx** | 一键导出真正可打开、可修改的 Word 文件 |
| **认识你的第 N 天** | 从「认识你的第 1 天」开始陪伴；模拟 7/30/100 天只覆盖展示日期，刷新即回真实天数 |
| **13 组素材 / 74 帧** | 待机、挥手、大笑、睡觉、工作、害羞等（奔跑、行走已禁用）；透明像素不挡桌面点击，Windows 原生拖动；单击奶龙或通过托盘打开清单；禁止奔跑/行走动作及自主移动 |
| **托盘 + 系统通知** | 关窗不退出、托盘重开、到点弹系统通知，证明它是真桌面软件而不是网页 |
| **游戏彩蛋** | 内置小游戏与可插拔策略层；模型挂了照常能玩，纯规则实时层兜底 |

**产品第一原则**：每位使用者独立培养，从干净的第 1 天开始。
本仓库不含任何预置数据、演示故事或假历史，现场展示的是本机真实养出来的状态。

---

## 二、快速开始（Windows / PowerShell）

```powershell
Set-Location '<项目目录>\apps\deskpet\desktop'
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
npm start
```

- 本机若存在环境变量 `ELECTRON_RUN_AS_NODE=1`，Electron 会走 Node 模式并报 `app undefined`，**必须先清掉**。Git Bash 用 `env -u ELECTRON_RUN_AS_NODE npm start`。
- **不要带 `NAIWA_SMOKE_TEST=1`**，那是自动自检、跑完自动退出。
- 后端由主进程自动拉起，第一次会等 Python spawn 几秒。起来后是桌面角落一个透明小奶龙，托盘里有图标，清单要点开才出现。

队员 clone 后重建环境：

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
cd apps/deskpet/desktop; npm install
```

---

## 三、模型设置（可选，默认关闭）

**默认纯规则版**：断网、没有 Key 也能完整走完主流程，这是硬闸门，不是降级后的残缺体验。

想启用模型版，在应用内「设置 → 配置 / 修改」填**使用者自己的** API Key，由主进程 `safeStorage`
加密保存，启动时解密后**只注入 Python 子进程的环境变量**，不进渲染进程、命令行参数、日志或仓库文件。
开关有三态：`关`（零调用、零模型模块加载）/ `自动`（默认，有 Key 才用，失败静默降级）/ `开`（强制模型，失败如实报错）。

- 模型只输出固定结构的 JSON，单次/每日成本有本地上限，失败会如实报 401 / 余额 / 网络原因而不是假装成功；
- 界面只显示 Key 的脱敏尾四位，支持检查与删除；
- **首次打开不会索要 Key**，桌宠会在合适时机主动开口引导，且随时可以「以后再说」。

---

## 四、目录地图

两个入口：根目录 `启动.bat` 启动桌宠；`python -m apps.agent` 或 `scripts/start-agent.bat` 启动通用 Agent。

```text
apps/
  deskpet/
    desktop/          Electron、页面、动画和素材
    backend/          FastAPI、待办、成长、游戏和模型网关
  agent/
    __main__.py       CLI 入口
    runtime/          Agent 编排、会话、终端展示与 Token 预估
    tools/            搜索、文档、代码及 RAG 工具适配
infrastructure/
  llm/                模型供应商、模型目录与基础计费
  cache/              通用缓存
  retrieval/          文档切分、向量存储与相似度检索
  observability.py    公共日志
config/               配置加载、模型目录
scripts/              启动与知识库导入脚本
tests/                按应用和基础设施分组
examples/             独立游戏示例
docs/                 交接与验收说明
```

依赖方向：应用可以使用基础设施，`infrastructure/` 和 `config/` 不得导入 `apps/`。
RAG 检索存储位于基础设施层；`rag_search` 工具注册位于 Agent 层。
Agent 专用统计和流式终端展示留在 Agent 内部，桌宠只依赖基础计费函数。

开发与验证：

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
npm install --prefix apps/deskpet/desktop
npm start --prefix apps/deskpet/desktop
python -m apps.agent
python -m pytest -q
npm test --prefix apps/deskpet/desktop
.\apps\deskpet\desktop\node_modules\electron\dist\electron.exe tests/desktop/test_pet_drag_electron.cjs
.\.venv\Scripts\python.exe docs/验收集/verify.py --level full --root .
```

底层分层由 `tests/infrastructure/test_boundaries.py` 检查。

---

## 五、验收与自检

```powershell
# 后端全量验收（22 项机械检查 + 全套 pytest + 真实 HTTP）
.\.venv\Scripts\python.exe docs\验收集\verify.py --level full --root .

# 桌面壳自检（自动跑完即退出，只用于出证据）
cd apps/deskpet/desktop
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
$env:NAIWA_SMOKE_TEST = '1'
.\node_modules\electron\dist\electron.exe .
Remove-Item Env:NAIWA_SMOKE_TEST -ErrorAction SilentlyContinue
```

跑完记得清掉 `NAIWA_SMOKE_TEST`，否则下次启动会又进自检。

当前基线：`full` 22/22、**249 passed**、路由冒烟 40 PASS / 0 FAIL、`model_calls=0`；
桌面壳自检 `DESKTOP_SMOKE pass=true`（初始态干净、13 动作 / 74 帧、动态端口仅回环、
关窗进托盘、系统通知、渲染进程 / 命令行 / 日志三处明文 Key 零泄漏）。

---

## 六、交接说明

> **接手开发请先读 [`docs/HANDOFF.md`](docs/HANDOFF.md)**：环境重建、自检判据、不许改的红线、阶段 6/7 待办（含打包 P0 的具体做法）都在那里。

### 上传 GitHub 前自检

1. 确认仓库里**没有 `.env`**（真实 Key 不能进仓库，也不能进阶段 7 打包的 exe）。
2. `.gitignore` 已忽略 `.env` / `.venv` / `apps/deskpet/desktop/node_modules/` / `data/` / 各类缓存；分批提交时用精确路径 `git add`，先 `git status` 核对。
3. `apps/deskpet/desktop/assets/nailong/` 必须随仓库保留（74 张 PNG + `manifest.json` + `ASSET-LICENSE.md`）；README 署名段、`docs/RELEASE.md`、应用内「关于」三处 CC BY 4.0 署名不可删。
4. 本仓库**刻意不包含 `.env`**；`.env.example` 只是占位符模板。桌宠运行不需要 `.env`，Key 由使用者在应用内填写。

### 当前进度与待办

阶段 1–5 已完成；提取后在本目录独立复跑 `verify.py --level full` 得 22/22、249 passed（20:16 瘦身删除 39 项 bridge 遗留测试后的基线），桌面壳自检 `pass:true`。
阶段 6（博客 / PPT / 需求文档 / 答辩）、阶段 7（打包 exe + GitHub Release）未开工。
答辩演示口径（11/6 19:58 定）：只展示本机真实状态三步 —— 规则版主流程 → 记忆补报 → 托盘通知；不演 CLI、不演完整 ReAct、不放录屏，也不预置任何演示数据。

已知待办：

1. `apps/deskpet/desktop/package.json` 的 `build.files` 缺 `apps/deskpet/backend/` 与 Python 运行时，按现状打包 exe 起不来；
2. README 尚无下载 / Release 入口（发布 exe 后补）；
3. `apps/deskpet/backend/llm.py` 有一处「待阶段 5 主进程注入」的过时文案（实际已接线）；
4. 打包须显式排除 `.env` 并验证启动链不加载它。

---

## 七、奶龙图像素材署名

桌宠图像与动作表来源：[zhangkk833-byte/dsh-nailong-pet](https://github.com/zhangkk833-byte/dsh-nailong-pet)；上游图像来源：[Maple498/nai-wa-codex-pet](https://github.com/Maple498/nai-wa-codex-pet)（Codex Pet v2，CC BY 4.0）。许可证：[Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/)。

本项目使用用户预先落地的 74 张 RGBA PNG、13 组 `manifest.json` 动作表和 `ASSET-LICENSE.md`，文件位于 `apps/deskpet/desktop/assets/nailong/`。上游已完成网格切分、地面线对齐与动画编排；本项目未改帧、未补帧、未调色、未修改像素，只新增本地播放与交互代码。上游不保证“奶龙”原型的形象权、商标权或其他第三方权利，本项目不宣称该形象为原创。完整授权说明见 `apps/deskpet/desktop/assets/nailong/ASSET-LICENSE.md`，同一署名也列于 `docs/RELEASE.md` 与应用内「关于」。

---

## License

MIT © 2026 马肇哲
