# 奶龙桌宠 · Desktop Pet

> 一个常驻 Windows 桌面的电子伙伴：贴一张通知 → 拆成清单 → 落成待办 →
> 下次回来主动补报「还差什么」→ 导出 docx。

一个**内嵌智能体决策内核**的桌面软件，而不是网页。桌宠提供场景、记忆与形态，智能体负责理解、判断与执行；两者通过同一份本机数据与同一个开关体系连在一起。

---

## 一、它能做什么

| 能力 | 说明 |
|---|---|
| **贴通知出清单** | 把一段作业通知拆成逐条可勾选的清单。模型档输出固定结构的 JSON，规则档兜底；陌生通知也能出结果，界面诚实标注「模型拆解 / 规则兜底」 |
| **跨会话记忆比对**（内核） | 记住上次交了什么，回来时主动补报「你以为交齐了，其实还差合照」——这是它和普通待办清单的区别 |
| **决策链可视化** | 把「原文 → 拆出哪些项 → 为什么判定还差」摊开给人看，不是黑箱一句话 |
| **导出 docx** | 一键导出真正可打开、可修改的 Word 文件，下载走白名单校验，导出物到期真清理 |
| **随便聊聊** | 单次结构化调用，只聊当前任务与本机真实记忆，工具表为 0 |
| **Agent 模式**（可选，默认关） | 完整工具链与联网能力：域名白名单 + 检索结果注入清洗，危险工具（命令行 / 写文件 / 改文件）需弹窗审批，60 秒未响应默认拒绝；与下方三态开关叠加，共用同一份成本账本 |
| **桌宠形态** | 13 组素材动作 / 74 帧 + 拟人动作调度层；透明像素不挡桌面点击，Windows 原生拖动；单击奶龙或从托盘打开清单，关窗不退出、到点弹系统通知 |
| **认识你的第 N 天** | 从「认识你的第 1 天」开始陪伴；模拟 7/30/100 天只覆盖展示日期，刷新即回真实天数 |

**产品第一原则**：每位使用者独立培养，从干净的第 1 天开始。
本仓库不含任何预置数据、演示故事或假历史，现场展示的是本机真实养出来的状态。

---

## 二、快速开始（Windows / PowerShell）

```powershell
Set-Location '<项目目录>\apps\deskpet\desktop'
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
npm start
```

也可以直接双击仓库根目录的 `启动.bat`（会检查 `.venv` 与 `node_modules`，缺依赖时自动安装）。Git Bash 用 `env -u ELECTRON_RUN_AS_NODE npm start`。

- 本机若存在环境变量 `ELECTRON_RUN_AS_NODE=1`，Electron 会走 Node 模式并报 `app undefined`，**必须先清掉**。
- **不要带 `NAIWA_SMOKE_TEST=1`**，那是自动自检、跑完自动退出。
- 后端由主进程自动拉起：本机 `.venv` 里的 Python 启动 FastAPI，只绑 `127.0.0.1` 的动态端口，第一次会等几秒。起来后是桌面角落一个透明小奶龙，清单要点开才出现。

clone 后重建环境：

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
cd apps\deskpet\desktop; npm install
```

---

## 三、模型与智能体设置（可选，默认关闭）

**默认纯规则版**：断网、没有 Key 也能完整走完主流程，这是硬闸门，不是降级后的残缺体验。

想启用模型版，在应用内「设置 → 配置 / 修改」填**使用者自己的** API Key，由主进程用 `safeStorage` 加密保存在本机，启动时解密后**只注入 Python 子进程的环境变量**，不进渲染进程、命令行参数、日志或仓库文件。界面只显示脱敏尾四位，支持检查与删除；**首次打开不会索要 Key**，桌宠会在合适时机主动开口引导，且随时可以「以后再说」。

开关分两层：

- **三态开关**：`关`（零调用、零模型模块加载）/ `自动`（默认，有 Key 才用，失败静默降级）/ `开`（强制模型，失败如实报错）。
- **Agent 模式**：叠加开关，默认关闭，打开后才启用完整工具链与联网能力；关态下相关模块完全不加载。

约束：模型只输出固定结构的 JSON，重试 ≤ 2 次；单次 / 每日成本有本地上限，失败会如实报 401 / 余额 / 网络原因而不是假装成功；能力注册表统一声明 `requires_network` / `requires_key` / `degraded_fallback` / `enabled`，不在各路由里各写一份判断。

---

## 四、目录地图

```text
apps/
  deskpet/
    desktop/          Electron 主进程、透明置顶窗、托盘与系统通知、Key/审批对话框
      renderer/       页面、闲聊与决策链、序列帧播放、动作调度与意图层
      assets/nailong/ 74 张 RGBA PNG + manifest.json + ASSET-LICENSE.md
      vendor/petex/   Windows 原生拖拽的参考实现（Apache-2.0）
    backend/          FastAPI、SQLite/WAL、待办与成长、跨会话补报、导出 docx
                      LlmGateway、能力注册表、规则兜底、完整 Agent 运行时
  agent/              桌宠内嵌 Agent 的工具与运行时实现（按需加载）
infrastructure/       模型供应商、缓存、检索与公共日志
config/               模型目录与配置
scripts/              辅助脚本
tests/                按 backend / desktop / agent / infrastructure 分组
docs/                 交接、发布与验收说明
```

依赖方向：`apps/` 可以使用 `infrastructure/` 与 `config/`，反过来不得导入 `apps/`；由 `tests/infrastructure/test_boundaries.py` 检查。

开发与验证：

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
npm install --prefix apps/deskpet/desktop
npm start --prefix apps/deskpet/desktop
python -m pytest -q
npm test --prefix apps/deskpet/desktop
.\.venv\Scripts\python.exe docs/验收集/verify.py --level full --root .
```

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

当前实测基线（2026-11-07）：

- `verify.py --level full`：**21/22**，唯一未绿项是 `pytest_green`（273 passed + 6 failed）。6 个失败全部来自 `tests/agent/test_registry.py` 里 import 路径未随目录重构同步，属测试代码自身，不影响软件运行。
- 接口契约冒烟 **34 PASS / 0 FAIL**；真实 HTTP 检查全绿（主页 / 下载 / 静态旁路 / 路径穿越 / 过期清理）；`model_calls=0`。
- 桌面壳 `DESKTOP_SMOKE pass=true`（退出码 0）：初始态干净、13 动作 / 74 帧、动态端口仅回环、关窗进托盘、系统通知、渲染进程 / 命令行 / 日志三处明文 Key 零泄漏；`npm test` **32/32**。

---

## 六、素材与第三方许可

**奶龙图像与动作表**：来源 [zhangkk833-byte/dsh-nailong-pet](https://github.com/zhangkk833-byte/dsh-nailong-pet)；上游图像来源 [Maple498/nai-wa-codex-pet](https://github.com/Maple498/nai-wa-codex-pet)（Codex Pet v2）。图像许可证 [Creative Commons Attribution 4.0 International（CC BY 4.0）](https://creativecommons.org/licenses/by/4.0/)。

本项目使用 74 张 RGBA PNG、13 组 `manifest.json` 动作表和 `ASSET-LICENSE.md`，文件位于 `apps/deskpet/desktop/assets/nailong/`。上游已完成网格切分、地面线对齐与动画编排；本项目未改帧、未补帧、未调色、未修改像素，只新增本地播放与交互代码。`meta.totalFrames=83` 是动作帧引用次数，74 是唯一 PNG 文件数。上游不保证「奶龙」原型的形象权、商标权或其他第三方权利，本项目不宣称该形象为原创。完整授权说明见 `apps/deskpet/desktop/assets/nailong/ASSET-LICENSE.md`，同一署名也列于 `docs/RELEASE.md` 与应用内「关于」。

**Windows 原生拖拽参考实现**：`apps/deskpet/desktop/vendor/petex/` 取自 [d4x1/petex](https://github.com/d4x1/petex)（commit `43981e2`），许可证 **Apache License 2.0**，随附 `LICENSE` 与 `SOURCE.md`；上游源文件保留，本项目仅改造了页面侧的指针生命周期与动画接入。

---

## License

本项目代码：MIT © 2026 马肇哲。第三方素材与代码各自按其上述许可证授权。
