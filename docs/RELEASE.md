# 发布说明 · 奶龙桌宠

> 本文件随仓库发布，说明本软件包含什么、第三方素材与代码如何授权，以及当前发布状态。
> 能力说明见根目录 `README.md`，开发交接见 `docs/HANDOFF.md`。

## 本软件包含什么

一个常驻 Windows 桌面的透明置顶小窗：贴一张通知 → 拆成清单 → 落成待办 → 下次回来主动补报「还差什么」→ 导出 docx。附托盘与系统通知、跨会话记忆、「认识你的第 N 天」成长展示。

- **默认规则版**：断网、没有 Key 也能完整走完主流程；模型可用时改用固定结构的 JSON 拆解，界面诚实标注「模型拆解 / 规则兜底」。
- **API Key 由使用者自填**：主进程 `safeStorage` 加密保存在本机，只注入 Python 子进程环境变量，不进仓库、包、日志或渲染进程。三态开关（关 / 自动 / 开）默认自动。
- **自由闲聊与 Agent 模式为可选能力，默认关闭**：Agent 模式启用完整工具链与联网能力，危险工具需弹窗审批、60 秒未响应默认拒绝，检索结果有域名白名单与注入清洗。
- **不含任何预置数据、演示故事或假历史**：每位使用者从干净的第 1 天独立培养，现场展示的是本机真实状态。

## 安装与启动（Windows，两步）

1. 从 [GitHub Releases](https://github.com/mazhaozhe/nailong-deskpet/releases) 下载下方 Assets 里的 `Setup.1.0.0.exe`（约 140 MB；本地构建产物名为 `奶龙桌宠 Setup 1.0.0.exe`，上传后 GitHub 会规范化附件名），双击安装（可自选安装目录）。
2. 安装完成后双击桌面上的「奶龙桌宠」。

**无需安装 Python、无需 pip、无需 API Key、无需联网**即可跑完整规则版主流程：贴通知 → 拆清单 → 落待办 → 导出 docx。安装包自带裁剪后的 Python 运行时（解释器 + 标准库 + 运行依赖），装完即用。

- **安装包体积约 140 MB**（解压后约 480 MB）：体积主要来自 Electron 运行时与内置 Python 运行环境。下载慢是正常的，请预留时间；GitHub Release 单文件上限 2 GB，不受影响。
- 安装到 `C:\Program Files` 这类只读目录也可以正常用：业务数据（待办、成长、导出物、成本账本）写在 `%APPDATA%\nailong-deskpet-client\deskpet\`，不写安装目录。
- 卸载时默认保留上面这个数据目录；要彻底清空请卸载后手动删除它。
- **Key 只在开模型档 / Agent 模式时才需要**：默认「自动」档没有 Key 就走规则版，不会报错、不会假装成功。Key 在应用内「设置」填写，由系统加密保存在本机，只注入本地 Python 子进程的环境变量，不进日志、不进渲染进程、不进安装包。
- **安装包未做代码签名，浏览器与 Windows 会有两道信誉提示，属于正常现象，不代表文件损坏**：
  1. **下载时**：Edge / Chrome 可能提示「通常不会下载 `Setup.1.0.0.exe`。请在打开前确保信任」。点下载项右侧 `…` → **保留** → 再点 **仍要保留**；或按 `Ctrl+J` 打开下载页，对被阻止的项执行同样的「保留」。
  2. **安装时**：双击后若出现蓝色的「Windows 已保护你的电脑」，点 **更多信息 → 仍要运行**。
  
  安装包只从本仓库 Release 分发，未经第三方修改。下载后可核对 SHA256 `a88ad46d00201f0483c44473bb4894ad004d1065f02729975521f87bce22aca0`（PowerShell：`Get-FileHash .\Setup.1.0.0.exe -Algorithm SHA256`）；与 Release 附件的 digest 一致即可放心。

## 奶龙图像素材与授权

- 素材与动作表来源：[zhangkk833-byte/dsh-nailong-pet](https://github.com/zhangkk833-byte/dsh-nailong-pet)。
- 上游图像来源：[Maple498/nai-wa-codex-pet](https://github.com/Maple498/nai-wa-codex-pet)（Codex Pet v2）。
- 图像许可证：[Creative Commons Attribution 4.0 International（CC BY 4.0）](https://creativecommons.org/licenses/by/4.0/)。
- 随包授权文件：`apps/deskpet/desktop/assets/nailong/ASSET-LICENSE.md`。

本项目使用 74 张 RGBA PNG、13 组 `manifest.json` 动作表及上述授权文件。`meta.totalFrames=83` 是动作帧引用次数，74 是唯一 PNG 文件数。上游已完成切分、裁切、地面线对齐与动作编排；本项目未改帧、未补帧、未调色、未修改任何像素，只新增本地序列帧播放和交互代码。「奶龙」原型的形象权、商标权或其他第三方权利不在上游授权保证范围内，本项目不宣称该形象为原创。相同署名同时放在根目录 `README.md` 与应用内「关于」。

## 第三方代码授权

`apps/deskpet/desktop/vendor/petex/`：Windows 原生拖拽参考实现，取自 [d4x1/petex](https://github.com/d4x1/petex)（commit `43981e2`），许可证 **Apache License 2.0**。随包保留 `LICENSE` 与 `SOURCE.md`；上游源文件原样保留，本项目只改造了页面侧的指针生命周期与动画接入。

本项目自身代码为 MIT（见根目录 `LICENSE`）；第三方素材与代码各自按其许可证授权。

## 当前发布状态

**阶段 7 已打包完成**：`npm run dist`（在 `apps/deskpet/desktop/` 下）产出 NSIS 安装包 `dist/奶龙桌宠 Setup 1.0.0.exe`，约 140 MB，内置 Python 运行时，使用者无需任何环境准备。

- 打包前会自动组装包内运行时（`scripts/assemble-runtime.mjs`，从本机 base Python 拷解释器 + 标准库 + DLL，再挂 `.venv` 的运行依赖），产物在 `apps/deskpet/desktop/build-runtime/`，不随仓库提交。
- 源码开发态仍走本机 `.venv`（`npm start` 不变）；打包态只认包内 `resources\runtime\python.exe`，缺了就提示重新安装，不会回退到系统 Python。
- **已发布 [v1.0.0](https://github.com/mazhaozhe/nailong-deskpet/releases/tag/v1.0.0)**：Release 页提供 `Setup.1.0.0.exe`（143,051,095 字节，约 140 MB），已核对远端附件与本机构建产物的 sha256 一致；GitHub 自动附带的 `Source code (zip/tar.gz)` 只是 tag 快照，主交付是 exe。
- 安装版实测：本机完成 NSIS 安装器双击安装，并在只读安装目录 + PATH 中剔除 Python 的环境下验证通过（安装版自检 `DESKTOP_SMOKE pass:true`、包内解释器 `import fastapi/uvicorn/docx/langchain` 通过、新建待办重启后仍在）。安装包未做代码签名，首次运行可能触发 SmartScreen；签名与杀软适配留待后续。
